#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path


POST_PATH_RE = re.compile(r"^/blog/(?P<year>\d{4})/(?P<month>\d{2})/.+/$")


@dataclass(frozen=True)
class RenderedPost:
    year: str
    month: str
    title: str
    path: str


class PostTitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_h1 = False
        self.h1_parts: list[str] = []
        self.og_title: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key: value or "" for key, value in attrs}
        if tag == "h1":
            self.in_h1 = True
        if tag == "meta" and attrs_map.get("property") == "og:title":
            self.og_title = attrs_map.get("content") or None

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1":
            self.in_h1 = False

    def handle_data(self, data: str) -> None:
        if self.in_h1:
            self.h1_parts.append(data)

    @property
    def title(self) -> str | None:
        h1 = " ".join(" ".join(self.h1_parts).split())
        return h1 or self.og_title


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize WordPress query redirect maps against Hugo's rendered canonical URLs."
    )
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument("--rendered-dir", default="public", help="Rendered Hugo output directory")
    parser.add_argument("--post-map-csv", default="data/wordpress-url-map.csv")
    parser.add_argument("--post-map-json", default="data/wordpress_id_map.json")
    parser.add_argument("--attachment-map-json", default="data/wordpress_attachment_id_map.json")
    parser.add_argument("--check", action="store_true", help="Report stale targets without writing files")
    return parser.parse_args()


def compact_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", html.unescape(value)).lower()
    return "".join(
        char
        for char in normalized
        if not unicodedata.category(char).startswith(("P", "S", "Z"))
    )


def rendered_posts(rendered_dir: Path) -> list[RenderedPost]:
    posts: list[RenderedPost] = []
    for index_path in sorted(rendered_dir.rglob("index.html")):
        parent = index_path.parent.relative_to(rendered_dir).as_posix()
        path = f"/{parent}/"
        match = POST_PATH_RE.match(path)
        if match is None:
            continue

        parser = PostTitleParser()
        parser.feed(index_path.read_text(encoding="utf-8", errors="ignore"))
        if parser.title is None:
            continue

        posts.append(
            RenderedPost(
                year=match.group("year"),
                month=match.group("month"),
                title=parser.title,
                path=path,
            )
        )
    return posts


def existing_rendered_path(rendered_dir: Path, site_path: str) -> bool:
    return (rendered_dir / site_path.strip("/") / "index.html").exists()


def unique_lookup(posts: list[RenderedPost]) -> tuple[dict[tuple[str, str, str], str], dict[tuple[str, str, str], str]]:
    exact_candidates: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    compact_candidates: dict[tuple[str, str, str], list[str]] = defaultdict(list)

    for post in posts:
        exact_candidates[(post.year, post.month, post.title.strip())].append(post.path)
        compact_candidates[(post.year, post.month, compact_title(post.title))].append(post.path)

    exact = {key: paths[0] for key, paths in exact_candidates.items() if len(paths) == 1}
    compact = {key: paths[0] for key, paths in compact_candidates.items() if len(paths) == 1}
    return exact, compact


def resolve_target(row: dict[str, str], rendered_dir: Path, exact: dict[tuple[str, str, str], str], compact: dict[tuple[str, str, str], str]) -> str:
    current_target = (row.get("new_url") or "").strip()
    if existing_rendered_path(rendered_dir, current_target):
        return current_target

    match = POST_PATH_RE.match(current_target)
    if match is None:
        return current_target

    title = (row.get("title") or "").strip()
    key = (match.group("year"), match.group("month"), title)
    if key in exact:
        return exact[key]

    compact_key = (match.group("year"), match.group("month"), compact_title(title))
    return compact.get(compact_key, current_target)


def write_json(path: Path, payload: dict[str, str]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.expanduser().resolve()
    rendered_dir = (repo_root / args.rendered_dir).resolve()
    post_map_csv = repo_root / args.post_map_csv
    post_map_json = repo_root / args.post_map_json
    attachment_map_json = repo_root / args.attachment_map_json

    if not rendered_dir.exists():
        raise RuntimeError(f"Rendered directory does not exist: {rendered_dir}")

    posts = rendered_posts(rendered_dir)
    exact, compact = unique_lookup(posts)

    with post_map_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = reader.fieldnames

    if not fieldnames:
        raise RuntimeError(f"CSV has no header: {post_map_csv}")

    rewrites: dict[str, str] = {}
    stale_after_normalize: list[tuple[str, str]] = []
    for row in rows:
        old_target = (row.get("new_url") or "").strip()
        new_target = resolve_target(row, rendered_dir, exact, compact)
        if new_target != old_target:
            rewrites[old_target] = new_target
            row["new_url"] = new_target
        if not existing_rendered_path(rendered_dir, new_target):
            stale_after_normalize.append(((row.get("wordpress_id") or "").strip(), new_target))

    post_map = {
        (row.get("wordpress_id") or "").strip(): (row.get("new_url") or "").strip()
        for row in rows
        if (row.get("wordpress_id") or "").strip() and (row.get("new_url") or "").strip()
    }

    attachment_map = json.loads(attachment_map_json.read_text(encoding="utf-8"))
    if not isinstance(attachment_map, dict):
        raise RuntimeError(f"Expected a JSON object in {attachment_map_json}")
    normalized_attachment_map = {
        str(key): rewrites.get(str(value), str(value))
        for key, value in attachment_map.items()
    }

    stale_attachments = [
        (key, value)
        for key, value in normalized_attachment_map.items()
        if not existing_rendered_path(rendered_dir, value)
    ]

    print(f"Rendered posts indexed: {len(posts)}")
    print(f"Post URL rewrites: {len(rewrites)}")
    print(f"Stale post targets after normalization: {len(stale_after_normalize)}")
    print(f"Stale attachment targets after normalization: {len(stale_attachments)}")

    if stale_after_normalize:
        for post_id, target in stale_after_normalize[:20]:
            print(f"  post {post_id}: {target}")
    if stale_attachments:
        for attachment_id, target in stale_attachments[:20]:
            print(f"  attachment {attachment_id}: {target}")

    if args.check:
        return 1 if stale_after_normalize or stale_attachments or rewrites else 0

    with post_map_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    write_json(post_map_json, post_map)
    write_json(attachment_map_json, normalized_attachment_map)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

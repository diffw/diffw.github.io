#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import subprocess
import tempfile
import shutil
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate static legacy redirect pages that point to canonical /blog URLs."
    )
    parser.add_argument(
        "--repo-root",
        default=Path(__file__).resolve().parents[1],
        type=Path,
        help="Repository root used when rendering Hugo",
    )
    parser.add_argument(
        "--static-dir",
        default=Path(__file__).resolve().parents[1] / "static",
        type=Path,
        help="Static directory where legacy /posts redirects should be written",
    )
    parser.add_argument(
        "--site-url",
        default="https://diff.im",
        help="Public site origin used in redirect targets",
    )
    parser.add_argument(
        "--legacy-path-map",
        default=Path(__file__).resolve().parents[1] / "data" / "legacy-path-redirects.csv",
        type=Path,
        help="CSV of GA/Search Console legacy paths that should redirect to current routes",
    )
    return parser.parse_args()


def build_redirect_html(target_url: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <title>Redirecting...</title>
    <meta http-equiv="refresh" content="0; url={target_url}">
    <link rel="canonical" href="{target_url}">
    <script async src="https://www.googletagmanager.com/gtag/js?id=G-WRGQE6EWMX"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){{dataLayer.push(arguments);}}
      gtag('js', new Date());
      gtag('config', 'G-WRGQE6EWMX');
    </script>
    <script>
      location.replace({target_url!r});
    </script>
  </head>
  <body>
    <p>Redirecting to <a href="{target_url}">{target_url}</a>.</p>
  </body>
</html>
"""


class CanonicalLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.canonical_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return

        attrs_map = {key: value or "" for key, value in attrs}
        if attrs_map.get("rel") == "canonical" and attrs_map.get("href"):
            self.canonical_href = attrs_map["href"]


def canonical_url_for_page(page: Path, fallback_url: str) -> str:
    parser = CanonicalLinkParser()
    parser.feed(page.read_text(encoding="utf-8", errors="ignore"))
    canonical_href = parser.canonical_href
    if not canonical_href:
        return fallback_url

    canonical_path = unquote(urlparse(canonical_href).path)
    fallback_path = unquote(urlparse(fallback_url).path)
    if canonical_path == fallback_path:
        return fallback_url

    parsed = urlparse(canonical_href)
    return urlunparse(parsed._replace(path=unquote(parsed.path)))


def normalize_site_path(path: str) -> str:
    path = unquote(path.strip())
    if not path.startswith("/"):
        path = f"/{path.lstrip('./')}"
    if path != "/" and not path.endswith("/") and not path.endswith(".html"):
        path = f"{path}/"
    return path


def rendered_page_for_site_path(output_dir: Path, path: str) -> Path:
    normalized = normalize_site_path(path)
    if normalized == "/":
        return output_dir / "index.html"
    if normalized.endswith(".html"):
        return output_dir / normalized.lstrip("/")
    return output_dir / normalized.lstrip("/") / "index.html"


def write_static_redirect_page(static_dir: Path, legacy_path: str, target_url: str) -> Path:
    normalized = normalize_site_path(legacy_path)
    if normalized == "/":
        raise ValueError("Refusing to overwrite the site root with a legacy redirect")
    if normalized.endswith(".html"):
        redirect_page = static_dir / normalized.lstrip("/")
    else:
        redirect_page = static_dir / normalized.lstrip("/") / "index.html"

    redirect_page.parent.mkdir(parents=True, exist_ok=True)
    redirect_page.write_text(build_redirect_html(target_url), encoding="utf-8")
    return redirect_page


def generate_legacy_path_redirects(
    *,
    legacy_path_map: Path,
    output_dir: Path,
    static_dir: Path,
    site_url: str,
) -> int:
    if not legacy_path_map.exists():
        return 0

    redirect_count = 0
    with legacy_path_map.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            legacy_path = normalize_site_path(row.get("legacy_path") or "")
            target_path = normalize_site_path(row.get("target_path") or "")
            if not legacy_path or not target_path:
                continue

            target_page = rendered_page_for_site_path(output_dir, target_path)
            if not target_page.exists():
                raise RuntimeError(
                    f"Legacy redirect target does not exist: {legacy_path} -> {target_path}"
                )

            existing_legacy_page = rendered_page_for_site_path(output_dir, legacy_path)
            if existing_legacy_page.exists():
                existing_canonical = canonical_url_for_page(
                    existing_legacy_page,
                    f"{site_url}{legacy_path}",
                )
                if unquote(urlparse(existing_canonical).path) == target_path:
                    continue
                raise RuntimeError(
                    f"Legacy path already renders a different page: {legacy_path}"
                )

            write_static_redirect_page(static_dir, legacy_path, f"{site_url}{target_path}")
            redirect_count += 1

    return redirect_count


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.expanduser().resolve()
    static_dir = args.static_dir.expanduser().resolve()
    legacy_path_map = args.legacy_path_map.expanduser().resolve()
    legacy_posts_dir = static_dir / "posts"
    site_url = args.site_url.rstrip("/")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        subprocess.run(
            ["hugo", "--quiet", "--destination", str(output_dir)],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        canonical_blog_dir = output_dir / "blog"

        if not canonical_blog_dir.exists():
            raise RuntimeError(f"Canonical blog output does not exist: {canonical_blog_dir}")

        if legacy_posts_dir.exists():
            shutil.rmtree(legacy_posts_dir)
        legacy_posts_dir.mkdir(parents=True, exist_ok=True)

        redirect_count = 0
        for canonical_page in canonical_blog_dir.rglob("index.html"):
            rel_dir = canonical_page.relative_to(canonical_blog_dir).parent
            redirect_dir = legacy_posts_dir / rel_dir
            redirect_dir.mkdir(parents=True, exist_ok=True)

            blog_path = "/blog/"
            if rel_dir != Path("."):
                blog_path = f"/blog/{rel_dir.as_posix().strip('/')}/"
            target_url = canonical_url_for_page(canonical_page, f"{site_url}{blog_path}")

            (redirect_dir / "index.html").write_text(build_redirect_html(target_url), encoding="utf-8")
            redirect_count += 1

        legacy_blog_redirect_count = generate_legacy_path_redirects(
            legacy_path_map=legacy_path_map,
            output_dir=output_dir,
            static_dir=static_dir,
            site_url=site_url,
        )

    print(f"Generated {redirect_count} legacy redirect pages under {legacy_posts_dir}")
    print(f"Generated {legacy_blog_redirect_count} GA legacy path redirect pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

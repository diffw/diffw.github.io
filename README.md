# diff-blog

Hugo 站点仓库，对应 GitHub 仓库 `diffw/diffw.github.io`，部署到 GitHub Pages，自定义域 `diff.im`。

## 角色分工

写作和发布跨两个仓库：

| 角色 | 位置 |
|---|---|
| 写作源（Obsidian + git） | 本地 `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/diff-blog-source` ↔ `github.com/diffw/diff-blog-source` |
| Hugo 站点（本仓库） | 本地 `~/NWA/diff-blog` ↔ `github.com/diffw/diffw.github.io` |
| 发布站 | GitHub Pages → `diff.im` |

约定：

- `diff-blog-source` 是唯一内容源。
- 本仓库的 `content/posts/` 和 `content/{about,links,now,projects}.md` 由同步流程写入，**不手工编辑**，避免双向漂移。
- 本仓库需要手工维护的是站点本身：`layouts/`、`static/`、`hugo.yaml`、workflow、`tests/` 等。

## 内容创作

在 Obsidian 的 `diff-blog-source` 目录里写 Markdown：

- 仓库根目录的 `*.md` → 文章（同步到本仓库的 `content/posts/`）。
- `pages/*.md` → 站点页面（同步到本仓库的 `content/`，目前是 about / links / now / projects）。
- `.blog-syncignore` 列出不发布的文件（一行一个文件名，目前只有 `template.md`）。

写完后用 Obsidian 的 git 插件 commit & push 到 `diffw/diff-blog-source`。

## 发布流程

两段 GitHub Actions 接力，全程在 GitHub 上跑，本机不需要常驻进程。

### 第一段：source 仓库的同步 workflow

`diff-blog-source/.github/workflows/sync-posts-to-blog.yml`，在 push 到 `main` 且改动了 `*.md` / `pages/*.md` / `.blog-syncignore` / 同步脚本时触发：

1. checkout `diff-blog-source`。
2. 用 `BLOG_REPO_TOKEN` checkout `diffw/diffw.github.io` 到 `blog-repo/`。
3. 跑 `scripts/sync-posts-to-blog.sh` 两次：
   - 根目录 `*.md` → `blog-repo/content/posts/`（参考 `.blog-syncignore`）。
   - `pages/*.md` → `blog-repo/content/`。
   - **镜像式同步**：源里删掉的文件会从目标里一并删除。
4. 如果 `blog-repo/content/` 有 diff，以 `github-actions[bot]` 身份提交 `Sync content from diff-blog-source@<sha>` 并 push 到本仓库 main。

### 第二段：本仓库的 Hugo 构建 workflow

`.github/workflows/hugo.yml`，监听本仓库 main 的 push（包括上一步 bot 的 push）和手动 dispatch：

1. 安装 Hugo 0.160.1 extended。
2. `hugo --minify` 构建到 `./public`。
3. 跑 `tests/test_rendered_site.py` 做产物健全性检查。
4. `upload-pages-artifact` + `deploy-pages` → GitHub Pages。

自定义域由 `CNAME`（`diff.im`）和 `hugo.yaml` 的 `baseURL: https://diff.im/` 决定。

## 本地预览

```bash
cd ~/NWA/diff-blog
hugo server -D
```

## 测试

```bash
cd ~/NWA/diff-blog
python3 -m unittest discover -s tests
```

## 旁支：SEO / WordPress 迁移产物

下面这些是从老 WordPress 站点迁移过来的副产物，不参与日常发文流程，按需手动跑：

- `scripts/generate_seo_migration_artifacts.py`、`scripts/generate_nginx_*.py`、`scripts/import_wordpress_xml.py`、`scripts/prepare_public_aliases.py`
- `data/wordpress_*_map.json`、`data/wordpress-url-map.csv`
- `ops/nginx/*.conf`
- `docs/seo-migration-audit.md`

## 历史遗留

`scripts/publish_from_obsidian.py`、`scripts/watch_diff_blog.py`、`scripts/start_autopublish.sh`、`ops/launchd/` 是上一版「本地 launchd watcher + debounce 自动发布」方案的产物，目前已被两段 GitHub Actions 取代，不再使用。

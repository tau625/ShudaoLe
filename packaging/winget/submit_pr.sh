#!/usr/bin/env bash
# submit_pr.sh —— 零克隆把 winget 清单提交到 microsoft/winget-pkgs
#
# 为什么不用 git clone：
#   winget-pkgs 仓库历史 866 MB，国内直连实测 pack 传输被限到 ~8.5 KB/s
#   （拉了 5 分钟只到 5.0 MB，之后长时间零增量），全量 clone 与 sparse clone
#   都走不通。本脚本全程只走 GitHub REST API，总流量几十 KB，国内网络稳定可用。
#
# 用法：
#   bash packaging/winget/submit_pr.sh                  # 提交 version_info.txt 里的版本
#   bash packaging/winget/submit_pr.sh 1.5.0            # 指定版本
#   bash packaging/winget/submit_pr.sh 1.5.0 --dry-run  # 只体检 + 打印计划，不碰 GitHub
#
# 前置：`gh auth status` 已登录且 token 含 repo 权限；
#       清单已由 gen_manifests.py 生成（且哈希对齐 Release）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

UPSTREAM="microsoft/winget-pkgs"
GITHUB_USER="${GITHUB_USER:-tau625}"
FORK="$GITHUB_USER/winget-pkgs"
PKG_REL="manifests/t/tau625/ShudaoLe"
VERSION_INFO="$REPO_ROOT/version_info.txt"

DRY_RUN=0
VERSION=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -*) echo "未知参数：$arg" >&2; exit 2 ;;
    *) VERSION="$arg" ;;
  esac
done

step() { printf '\n\033[36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 1. 版本号：单一数据源 version_info.txt ────────────────────────────────
if [ -z "$VERSION" ]; then
  VERSION="$( { grep -oP "StringStruct\(u'FileVersion',\s*u'\K[^']+" "$VERSION_INFO" || true; } | head -1 )"
fi
[ -n "$VERSION" ] || die "无法从 $VERSION_INFO 解析版本号，请显式传入一个版本"

SRC_DIR="$SCRIPT_DIR/manifests/t/tau625/ShudaoLe/$VERSION"
BRANCH="tau625.ShudaoLe-$VERSION"
TITLE="New package: tau625.ShudaoLe version $VERSION"

echo "包名        : tau625.ShudaoLe"
echo "版本        : $VERSION"
echo "源目录      : $SRC_DIR"
echo "目标分支    : $BRANCH ($FORK)"
echo "上游        : $UPSTREAM (base: master)"
if [ "$DRY_RUN" = 1 ]; then
  echo "模式        : DRY-RUN（不会产生任何 GitHub 操作）"
fi

# ── 2. 体检 ──────────────────────────────────────────────────────────────
step "体检"

gh auth status >/dev/null 2>&1 || die "gh 未登录，先跑 gh auth login"
ok "gh 已登录"

[ -d "$SRC_DIR" ] || die "清单目录不存在：$SRC_DIR（先跑 gen_manifests.py --fetch）"
FILES=("$SRC_DIR"/*.yaml)
[ -f "${FILES[0]}" ] || die "清单目录里没有 yaml：$SRC_DIR"
ok "找到 ${#FILES[@]} 个清单文件：$(for f in "${FILES[@]}"; do basename "$f"; done | tr '\n' ' ')"

for f in "${FILES[@]}"; do
  grep -q "PackageVersion: *$VERSION" "$f" 2>/dev/null || true
done

if command -v winget >/dev/null 2>&1; then
  # winget 是 Windows 程序，只认 D:\... 形式，得把 Git Bash 的 /d/... 转过去
  VAL_DIR="$SRC_DIR"
  command -v cygpath >/dev/null 2>&1 && VAL_DIR="$(cygpath -w "$SRC_DIR")"
  if winget validate --manifest "$VAL_DIR" >/dev/null 2>&1; then
    ok "winget validate 通过"
  else
    warn "winget validate 未通过——先修好再提交："
    winget validate --manifest "$VAL_DIR" 2>&1 | sed 's/^/      /' | head -20
    die "清单元数据不合法，中止"
  fi
else
  warn "本机没有 winget，跳过 validate"
fi

if [ "$DRY_RUN" = 1 ]; then
  step "DRY-RUN 计划"
  cat <<EOF
  1. gh repo view $FORK   （不存在则 gh repo fork $UPSTREAM --clone=false）
  2. gh repo sync $FORK --branch master
  3. 取 master SHA → 建分支 refs/heads/$BRANCH
  4. 逐文件 PUT repos/$FORK/contents/$PKG_REL/$VERSION/<name>
       · $(for f in "${FILES[@]}"; do basename "$f"; done | tr '\n' ' ')
  5. gh pr create --repo $UPSTREAM --base master --head $GITHUB_USER:$BRANCH
       · title: $TITLE
EOF
  exit 0
fi

# ── 3. fork ─────────────────────────────────────────────────────────────
step "准备 fork"
if gh repo view "$FORK" >/dev/null 2>&1; then
  ok "fork 已存在：$FORK"
  gh repo sync "$FORK" --branch master >/dev/null 2>&1 && ok "已同步 master" || warn "同步失败，沿用现状"
else
  gh repo fork "$UPSTREAM" --clone=false >/dev/null 2>&1 || die "fork 失败"
  ok "已创建 fork：$FORK"
  for _ in $(seq 1 12); do
    gh repo view "$FORK" >/dev/null 2>&1 && break
    sleep 5
  done
fi

# ── 4. 分支 ─────────────────────────────────────────────────────────────
step "建分支 $BRANCH"
gh api -X DELETE "repos/$FORK/git/refs/heads/$BRANCH" >/dev/null 2>&1 && warn "已清理同名旧分支" || true
BASE_SHA="$(gh api "repos/$FORK/git/ref/heads/master" --jq .object.sha)" || die "取 master SHA 失败"
[ -n "$BASE_SHA" ] || die "master SHA 为空"
gh api -X POST "repos/$FORK/git/refs" -f ref="refs/heads/$BRANCH" -f sha="$BASE_SHA" >/dev/null \
  || die "建分支失败"
ok "分支就绪（base ${BASE_SHA:0:7}）"

# ── 5. 上传文件（Contents API，每个文件一个 commit） ──────────────────────
step "上传清单"
for f in "${FILES[@]}"; do
  name="$(basename "$f")"
  rel="$PKG_REL/$VERSION/$name"
  existing="$(gh api "repos/$FORK/contents/$rel?ref=$BRANCH" --jq .sha 2>/dev/null || true)"
  content="$(base64 -w0 "$f")"
  if [ -n "$existing" ]; then
    gh api -X PUT "repos/$FORK/contents/$rel" \
      -f message="$TITLE" -f branch="$BRANCH" -f content="$content" -f sha="$existing" >/dev/null
  else
    gh api -X PUT "repos/$FORK/contents/$rel" \
      -f message="$TITLE" -f branch="$BRANCH" -f content="$content" >/dev/null
  fi
  ok "$rel"
done

# ── 6. 开 PR ────────────────────────────────────────────────────────────
step "创建 PR"
BODY="Adds **书到了 (ShudaoLe)** — a desktop tool that downloads textbook PDFs from China's National Smart Education Platform (国家中小学智慧教育平台).

- Publisher: \`tau625\`
- Homepage: https://github.com/${GITHUB_USER}/ShudaoLe
- Installer: Inno Setup (\`ShudaoLe-${VERSION}-setup.exe\`) from GitHub Releases
- Silent install supported (\`/VERYSILENT\`), per-machine (\`{autopf}\`, requires elevation)

###### Microsoft CLA
By submitting this pull request, I confirm I have read and agree to the Microsoft CLA."

PR_URL="$(gh pr create --repo "$UPSTREAM" --base master \
  --head "$GITHUB_USER:$BRANCH" \
  --title "$TITLE" --body "$BODY" 2>&1)" || die "PR 创建失败：$PR_URL"

ok "PR 已创建：$PR_URL"
cat <<EOF

下一步：
  · PR 页面上按 bot 提示签微软 CLA（网页点一下）
  · 自动验证会校验 schema + InstallerSha256 与 URL 文件是否一致 + 恶意软件扫描
  · 通过后进入人工审核，一般 1~7 天

重跑说明：本脚本幂等——会先删掉同名分支再重建，可放心重跑。
EOF

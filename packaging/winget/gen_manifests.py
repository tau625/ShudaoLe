#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 winget-pkgs 提交用的三份清单 YAML（version / installer / defaultLocale）。

用法（每次发版后在仓库根目录执行）：

    # 自动从 GitHub Release 的 SHA256SUMS.txt 取安装器哈希（走 gh CLI）
    python packaging/winget/gen_manifests.py --fetch

    # 或手动指定哈希
    python packaging/winget/gen_manifests.py --sha256 <hex>

    # 也可以本地直接对刚下载的安装器算哈希
    python packaging/winget/gen_manifests.py --file ShudaoLe-1.3.8-setup.exe

产物：packaging/winget/manifests/t/tau625/ShudaoLe/<version>/*.yaml

提交流程见同目录 README.md。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_INFO = REPO_ROOT / "version_info.txt"

REPO = "tau625/ShudaoLe"
REPO_URL = f"https://github.com/{REPO}"
PACKAGE_ID = "tau625.ShudaoLe"
PUBLISHER = "tau625"
PACKAGE_NAME = "ShudaoLe (Book Arrived)"
LICENSE_NAME = "PolyForm Noncommercial 1.0.0"
LICENSE_URL = f"{REPO_URL}/blob/main/LICENSE"
# 必须与 installer.iss 的 UninstallDisplayName 完全一致（含全角括号）：
# Inno 会把它写进 ARP 注册表 DisplayName，winget 靠它匹配已安装应用
ARP_DISPLAY_NAME = "书到了（ShudaoLe）"
# winget-pkgs 审核推荐 1.12.0（1.10.0 也接受，更旧的 1.9 会被 bot 挑）
MANIFEST_VERSION = "1.12.0"
SCHEMA_BASE = f"https://aka.ms/winget-manifest.{{}}.{MANIFEST_VERSION}.schema.json"

VERSION_RE = re.compile(r"StringStruct\(u'FileVersion',\s*u'([^']+)'\)")


def read_version() -> str:
    """从 version_info.txt 解析当前版本号（与 exe 属性同源，勿另设来源）。"""
    text = VERSION_INFO.read_text(encoding="utf-8")
    m = VERSION_RE.search(text)
    if not m:
        sys.exit(f"无法从 {VERSION_INFO} 解析 FileVersion")
    return m.group(1)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _gh_token() -> str | None:
    """gh 自己的登录态在部分环境不持久；优先 GH_TOKEN，否则从 git credential 取。"""
    if os.environ.get("GH_TOKEN"):
        return None  # gh 已能拿到，无需注入
    try:
        out = subprocess.run(
            ["git", "credential", "fill"],
            input=b"protocol=https\nhost=github.com\n\n",
            capture_output=True,
        ).stdout.decode("utf-8", "replace")
        for line in out.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1]
    except OSError:
        pass
    return None


def fetch_setup_sha256(version: str) -> str:
    """从 Release 的 SHA256SUMS.txt 取安装器哈希：先试直接下载，失败走 gh API。"""
    url = f"{REPO_URL}/releases/download/v{version}/SHA256SUMS.txt"
    try:
        import urllib.request
        text = urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
    except OSError:
        text = _gh_download_sums(version)

    for line in text.splitlines():
        digest, _, name = line.partition("  ")
        if name.strip() == f"ShudaoLe-{version}-setup.exe":
            return digest.strip()
    sys.exit(f"SHA256SUMS.txt 里没有 ShudaoLe-{version}-setup.exe")


def _gh_download_sums(version: str) -> str:
    import os
    GH = r"C:\Program Files\GitHub CLI\gh.exe"
    env = dict(os.environ)
    if not env.get("GH_TOKEN"):
        tok = _gh_token()
        if tok:
            env["GH_TOKEN"] = tok
    r = subprocess.run(
        [GH, "release", "download", f"v{version}", "--repo", REPO,
         "--pattern", "SHA256SUMS.txt", "--output", "-", "--clobber"],
        capture_output=True, env=env,
    )
    if r.returncode != 0:
        sys.exit(f"获取 SHA256SUMS.txt 失败：{r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout.decode("utf-8")


def manifests(version: str, sha256: str) -> dict[str, str]:
    tag = f"v{version}"
    installer_url = f"{REPO_URL}/releases/download/{tag}/ShudaoLe-{version}-setup.exe"

    version_yaml = f"""# yaml-language-server: $schema={SCHEMA_BASE.format("version")}
PackageIdentifier: {PACKAGE_ID}
PackageVersion: {version}
DefaultLocale: en-US
ManifestType: version
ManifestVersion: {MANIFEST_VERSION}
"""

    installer_yaml = f"""# yaml-language-server: $schema={SCHEMA_BASE.format("installer")}
PackageIdentifier: {PACKAGE_ID}
PackageVersion: {version}
InstallerType: inno
InstallModes:
- interactive
- silent
- silentWithProgress
InstallerSwitches:
  Silent: /VERYSILENT /NORESTART
  SilentWithProgress: /SILENT /NORESTART
UpgradeBehavior: install
Installers:
- Architecture: x64
  InstallerUrl: {installer_url}
  InstallerSha256: {sha256}
  ElevationRequirement: elevationRequired
  AppsAndFeaturesEntries:
  - DisplayName: {ARP_DISPLAY_NAME}
    Publisher: {PUBLISHER}
    DisplayVersion: {version}
ManifestType: installer
ManifestVersion: {MANIFEST_VERSION}
"""

    locale_yaml = f"""# yaml-language-server: $schema={SCHEMA_BASE.format("defaultLocale")}
PackageIdentifier: {PACKAGE_ID}
PackageVersion: {version}
PackageLocale: en-US
Publisher: {PUBLISHER}
PublisherUrl: https://github.com/{PUBLISHER}
PublisherSupportUrl: {REPO_URL}/issues
PackageName: {PACKAGE_NAME}
PackageUrl: {REPO_URL}
License: {LICENSE_NAME}
LicenseUrl: {LICENSE_URL}
Copyright: Copyright (c) {date.today().year} {PUBLISHER}
ShortDescription: One-click official textbook PDF downloader for China's Smart Education platform
Description: >-
  ShudaoLe (Book Arrived) is a free desktop tool for teachers, students and
  parents to browse and batch-download official K-12 textbook PDFs from
  China's national Smart Education of Primary and Secondary School platform.
  It filters by education stage, grade, subject and publisher, and serves a
  local web UI in your browser — no browser extension, no account, no
  third-party mirrors.
Moniker: shudaole
Tags:
- education
- textbook
- pdf
- downloader
ManifestType: defaultLocale
ManifestVersion: {MANIFEST_VERSION}
"""
    return {
        f"{PACKAGE_ID}.yaml": version_yaml,
        f"{PACKAGE_ID}.installer.yaml": installer_yaml,
        f"{PACKAGE_ID}.locale.en-US.yaml": locale_yaml,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", help="覆盖版本号（默认从 version_info.txt 读取）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--sha256", help="安装器 SHA256（小写 hex）")
    g.add_argument("--file", help="本地安装器路径，现场计算 SHA256")
    g.add_argument("--fetch", action="store_true", help="从 GitHub Release 的 SHA256SUMS.txt 获取")
    args = ap.parse_args()

    version = args.version or read_version()

    if args.sha256:
        sha256 = args.sha256.lower()
    elif args.file:
        sha256 = sha256_of(Path(args.file))
    elif args.fetch:
        sha256 = fetch_setup_sha256(version)
    else:
        local = REPO_ROOT / f"ShudaoLe-{version}-setup.exe"
        if local.exists():
            sha256 = sha256_of(local)
        else:
            sha256 = fetch_setup_sha256(version)

    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        sys.exit(f"SHA256 格式不合法：{sha256!r}")

    out_dir = Path(__file__).resolve().parent / "manifests" / "t" / "tau625" / "ShudaoLe" / version
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in manifests(version, sha256).items():
        (out_dir / name).write_text(content, encoding="utf-8", newline="\n")
        print(f"写入 {out_dir / name}")
    print(f"\n下一步：fork microsoft/winget-pkgs 后按 README.md 提交 PR（commit 用 "
          f"\"Add version: {PACKAGE_ID} version {version}\"）")


if __name__ == "__main__":
    main()

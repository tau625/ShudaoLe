# -*- coding: utf-8 -*-
"""应用内更新检查与自动更新（P2-5 + v1.3.5 扩展）。

设计要点（已定方案）：
  - 只查版本号：GET /repos/tau625/ShudaoLe/releases/latest，无遥测/PII
  - 走系统代理；国内网络不稳时回退镜像前缀（用户可在设置中改）
  - 有新版 → 界面横幅：「查看新版」（跳 Release 页）+「一键下载」zip 到本地
  - v1.3.5 起新增「自动更新」通道（用户点击后才启动，绝不后台偷跑）：
      按平台挑选 Release 附件 → 流式下载到本地缓存（实时进度）→
      SHA256 校验（SHA256SUMS.txt）→ Windows 走安装器静默安装；
      macOS/Linux 下载到本地并打开所在目录（解压替换，不由程序自替换）。
  - 任何一步失败：状态置 error 并可重试，绝不影响当前版本继续使用。
  - 检查失败静默（横幅不出现）；默认启动后异步查一次，界面手动按钮可再查
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

RELEASE_API = "https://api.github.com/repos/tau625/ShudaoLe/releases/latest"
MIRROR_ENV = "SHUDAOLE_UPDATE_MIRROR"   # 例：https://ghproxy.net/   （前缀式镜像）
DISABLE_ENV = "SHUDAOLE_NO_UPDATE_CHECK"
TIMEOUT = 10

_cache = {"checked_at": 0.0, "result": None, "lock": threading.Lock()}


def _mirror_prefix():
    return (os.environ.get(MIRROR_ENV) or "").strip().rstrip("/")


def _version_tuple(v):
    parts = []
    for x in re.findall(r"\d+", v or ""):
        try:
            parts.append(int(x))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def check_newer(latest, current):
    """语义化版本比较：latest > current 才提示。"""
    return _version_tuple(latest) > _version_tuple(current)


def platform_key():
    """当前平台标识：windows / macos / linux（前端按钮文案与资产选择共用）。"""
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def pick_asset(names):
    """按平台从附件文件名列表里挑更新包（原生安装包优先）。

    windows 优先安装器（-setup.exe，可静默安装），没有则退回 windows-x64.zip；
    macos 优先 -macos.dmg（内含 .app），退回 -macos.zip；
    linux 优先 -linux-x64.deb（Debian/Ubuntu 双击即装），退回 tar.gz。
    找不到返回 None。
    """
    names = [n for n in names if n]
    if platform_key() == "windows":
        for n in names:
            if n.endswith("-setup.exe"):
                return n
        for n in names:
            if "windows-x64" in n and n.endswith(".zip"):
                return n
        return None
    if platform_key() == "macos":
        prefer, fallback = "-macos.dmg", "-macos.zip"
    else:
        prefer, fallback = "-linux-x64.deb", "-linux-x64.tar.gz"
    for n in names:
        if n.endswith(prefer):
            return n
    for n in names:
        if n.endswith(fallback):
            return n
    return None


def _checksums_for(assets, name):
    """从 SHA256SUMS.txt 附件内容里取 name 的期望哈希；没有则 None。

    assets: [{name, ...}]；SUMS 内容按需下载（文本小，直连+镜像各试一次）。
    """
    import urllib.request

    sums_url = None
    for a in assets:
        if (a.get("name") or "") == "SHA256SUMS.txt":
            sums_url = a.get("browser_download_url")
            break
    if not sums_url:
        return None
    for url in ([sums_url] + ([_mirror_prefix() + sums_url] if _mirror_prefix() else [])):
        try:
            opener = urllib.request.build_opener()
            with opener.open(url, timeout=TIMEOUT) as r:
                text = r.read().decode("utf-8", "replace")
            for line in text.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1].lstrip("*") == name:
                    return parts[0].lower()
        except Exception:
            continue
    return None


def verify_sha256(path, expected):
    """校验文件 SHA256；expected 为空/None 时跳过（返回 True）。"""
    if not expected:
        return True
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().lower() == expected.lower()


def download_update(asset_url, asset_name, expected_sha=None, dest_dir=None,
                    on_progress=None, cancel_check=None):
    """流式下载更新包到本地（.part 临时文件，完成后原子改名）。

    on_progress(done, total) 周期回调；cancel_check() 返回 True 时中止。
    返回 (本地路径, "") 或 ("", 错误消息)。下载地址只接受 GitHub Release 附件。
    """
    import urllib.parse
    import urllib.request

    host = (urllib.parse.urlsplit(asset_url).hostname or "").lower()
    if urllib.parse.urlsplit(asset_url).scheme != "https" or not (
            host == "github.com" or host == "objects.githubusercontent.com"
            or host.endswith(".github.com")):
        return "", "更新包地址不是 GitHub 官方链接，已拒绝下载"

    dest_dir = Path(dest_dir) if dest_dir else Path.home() / ".config" / "shudaole" / "updates"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return "", f"更新缓存目录创建失败: {e}"
    dest = dest_dir / asset_name
    part = dest_dir / (asset_name + ".part")

    urls = [asset_url]
    if _mirror_prefix():
        urls.append(_mirror_prefix() + asset_url)
    last_err = ""
    for url in urls:
        try:
            opener = urllib.request.build_opener()
            req = urllib.request.Request(url, headers={"User-Agent": "ShudaoLe-updater"})
            with opener.open(req, timeout=60) as r:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                with open(part, "wb") as f:
                    while True:
                        if cancel_check and cancel_check():
                            part.unlink(missing_ok=True)
                            return "", "已取消"
                        chunk = r.read(1 << 18)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if on_progress:
                            try:
                                on_progress(done, total)
                            except Exception:
                                pass
            if total and done != total:
                last_err = f"下载不完整（{done}/{total} 字节）"
                part.unlink(missing_ok=True)
                continue
            if not verify_sha256(part, expected_sha):
                last_err = "SHA256 校验失败：文件与官方发布不一致，已删除"
                part.unlink(missing_ok=True)
                continue
            shutil.move(str(part), str(dest))
            return str(dest), ""
        except Exception as e:
            last_err = f"下载失败: {e}"
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            continue
    return "", last_err or "下载失败"


def start_windows_installer(installer_path):
    """启动 Inno 安装器静默安装（只显示进度条，不显示向导）。

    安装器是 PrivilegesRequired=admin，启动时必定弹 UAC：
      - UAC 通过 -> CreateProcess 成功，安装器接管升级；
      - UAC 取消 / 当前账户无权提权 -> CreateProcess 直接失败（ERROR_ELEVATION_REQUIRED），
        这里会抛 OSError，必须如实报错。否则前端照常显示「安装器已启动」，
        用户以为在装了，实际什么都没发生（v1.3.7 之前的静默失败正是这么来的）。

    不传 /SUPPRESSMSGBOXES：它在静默安装出错时会连 Inno 的错误框一起吞掉，
    故障无痕最难排查；/SILENT 本身已经足够安静（只剩进度条）。
    不传 /RESTARTAPPLICATIONS：与 installer.iss [Run] 的启动项重复，
    会导致新版本被启动两次。
    返回 (ok, err)。
    """
    if not os.path.isfile(installer_path):
        return False, "安装包不存在"
    try:
        subprocess.Popen([
            installer_path, "/SILENT", "/NOCANCEL", "/FORCECLOSEAPPLICATIONS",
        ], close_fds=True)
        return True, ""
    except OSError as e:
        return False, (f"安装器未能启动（UAC 未确认或账户无权安装时会这样）: {e}；"
                       f"可到发布页手动下载安装")


def check_latest(current_version, force=False):
    """查询最新版本。返回 None（失败/禁用/无更新）或
    {latest, html_url, zip_url, asset_size}（有新版）。线程安全，60s 内复用缓存。"""
    import json
    import time as _time
    import urllib.request

    if os.environ.get(DISABLE_ENV):
        return None
    with _cache["lock"]:
        now = _time.time()
        if not force and _cache["result"] is not None and now - _cache["checked_at"] < 60:
            cached = _cache["result"]
            return cached if cached and check_newer(cached.get("latest", ""), current_version) else None

    # 直连查询（尊重系统代理）
    data = None
    for url in ([RELEASE_API] + ([_mirror_prefix() + RELEASE_API] if _mirror_prefix() else [])):
        try:
            opener = urllib.request.build_opener()  # 系统代理生效
            with opener.open(url, timeout=TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
            break
        except Exception:
            continue
    if not isinstance(data, dict):
        return None

    latest = (data.get("tag_name") or "").lstrip("vV")
    assets = [
        {"name": a.get("name") or "", "url": a.get("browser_download_url") or "",
         "size": a.get("size") or 0}
        for a in (data.get("assets") or []) if a.get("name")
    ]
    chosen = pick_asset([a["name"] for a in assets])
    zip_url = None
    asset_size = 0
    asset_name = ""
    if chosen:
        for a in assets:
            if a["name"] == chosen:
                zip_url = a["url"]
                asset_size = a["size"]
                asset_name = chosen
                break

    result = {
        "latest": latest,
        "html_url": data.get("html_url") or
            "https://github.com/tau625/ShudaoLe/releases/latest",
        "zip_url": zip_url,       # 兼容旧字段：本平台更新包下载地址（可能为 None）
        "asset_size": asset_size,
        "asset_name": asset_name,
        "platform": platform_key(),
        "assets": assets,         # 全部附件（SHA256SUMS.txt 查找用；不含二进制内容）
    }
    with _cache["lock"]:
        _cache["checked_at"] = _time.time()
        _cache["result"] = result
    return result if check_newer(latest, current_version) else None


def check_async(current_version, callback, force=False):
    """异步检查（GUI 启动用）：结果通过 callback(result_or_None) 回主线程。"""
    def _run():
        try:
            res = check_latest(current_version, force=force)
        except Exception:
            res = None
        try:
            if callback:
                callback(res)
        except Exception:
            pass
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t

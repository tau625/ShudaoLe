# -*- coding: utf-8 -*-
"""应用内更新检查（P2-5）。

设计要点（已定方案）：
  - 只查版本号：GET /repos/tau625/ShudaoLe/releases/latest，无遥测/PII
  - 走系统代理；国内网络不稳时回退镜像前缀（用户可在设置中改）
  - 有新版 → 界面横幅：「查看新版」（跳 Release 页）+「一键下载」zip 到本地
  - 下载完成提示手动解压替换；绝不自动替换 exe（杀软误报+法律双雷）
  - 检查失败静默（横幅不出现）；默认启动后异步查一次，界面手动按钮可再查
"""
import os
import re
import threading

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
    zip_url = None
    asset_size = 0
    for a in data.get("assets") or []:
        name = a.get("name") or ""
        if "windows-x64" in name and name.endswith(".zip"):
            zip_url = a.get("browser_download_url")
            asset_size = a.get("size") or 0
            break

    result = {
        "latest": latest,
        "html_url": data.get("html_url") or
            "https://github.com/tau625/ShudaoLe/releases/latest",
        "zip_url": zip_url,       # 可能为 None（该版本无 Windows zip 时只提示跳转）
        "asset_size": asset_size,
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

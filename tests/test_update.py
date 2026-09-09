# -*- coding: utf-8 -*-
"""shudaole.update 自动更新模块的纯函数测试（不发真实网络请求）。"""
import hashlib

from shudaole import update as upd


# ---------- 平台标识 ----------

def test_platform_key_is_one_of_three(monkeypatch):
    monkeypatch.setattr(upd.os, "name", "posix")
    monkeypatch.setattr(upd.sys, "platform", "darwin")
    assert upd.platform_key() == "macos"
    monkeypatch.setattr(upd.sys, "platform", "linux")
    assert upd.platform_key() == "linux"
    monkeypatch.setattr(upd.os, "name", "nt")
    assert upd.platform_key() == "windows"


# ---------- 附件选择 ----------

ASSETS = [
    "ShudaoLe-1.3.5-setup.exe",
    "ShudaoLe-1.3.5-windows-x64.zip",
    "ShudaoLe-1.3.5-macos.dmg",
    "ShudaoLe-1.3.5-macos.zip",
    "ShudaoLe-1.3.5-linux-x64.deb",
    "ShudaoLe-1.3.5-linux-x64.tar.gz",
    "SHA256SUMS.txt",
]


def test_pick_asset_windows_prefers_installer(monkeypatch):
    monkeypatch.setattr(upd, "platform_key", lambda: "windows")
    assert upd.pick_asset(ASSETS) == "ShudaoLe-1.3.5-setup.exe"
    # 没有安装器时退回绿色版 zip
    assert upd.pick_asset([n for n in ASSETS if "setup" not in n]) == \
        "ShudaoLe-1.3.5-windows-x64.zip"


def test_pick_asset_mac_linux(monkeypatch):
    # mac/linux 原生安装包优先（dmg / deb）
    monkeypatch.setattr(upd, "platform_key", lambda: "macos")
    assert upd.pick_asset(ASSETS) == "ShudaoLe-1.3.5-macos.dmg"
    monkeypatch.setattr(upd, "platform_key", lambda: "linux")
    assert upd.pick_asset(ASSETS) == "ShudaoLe-1.3.5-linux-x64.deb"


def test_pick_asset_fallback_to_archive(monkeypatch):
    # 没有原生包时回退压缩包（macos.zip / linux tar.gz）
    archives = ["ShudaoLe-1.3.5-macos.zip", "ShudaoLe-1.3.5-linux-x64.tar.gz"]
    monkeypatch.setattr(upd, "platform_key", lambda: "macos")
    assert upd.pick_asset(archives) == "ShudaoLe-1.3.5-macos.zip"
    monkeypatch.setattr(upd, "platform_key", lambda: "linux")
    assert upd.pick_asset(archives) == "ShudaoLe-1.3.5-linux-x64.tar.gz"


def test_pick_asset_missing_returns_none(monkeypatch):
    monkeypatch.setattr(upd, "platform_key", lambda: "macos")
    assert upd.pick_asset(["ShudaoLe-1.3.5-setup.exe"]) is None
    assert upd.pick_asset([]) is None


# ---------- SHA256 校验 ----------

def test_verify_sha256_ok_and_mismatch(tmp_path):
    f = tmp_path / "pkg.bin"
    f.write_bytes(b"hello shudaole")
    digest = hashlib.sha256(b"hello shudaole").hexdigest()
    assert upd.verify_sha256(f, digest) is True
    assert upd.verify_sha256(f, digest.upper()) is True  # 大小写不敏感
    assert upd.verify_sha256(f, "0" * 64) is False


def test_verify_sha256_empty_expected_skips(tmp_path):
    f = tmp_path / "pkg.bin"
    f.write_bytes(b"x")
    assert upd.verify_sha256(f, None) is True
    assert upd.verify_sha256(f, "") is True


# ---------- 版本比较（回归保护） ----------

def test_check_newer_semver():
    assert upd.check_newer("1.3.5", "1.3.4") is True
    assert upd.check_newer("1.4.0", "1.3.10") is True
    assert upd.check_newer("1.3.4", "1.3.4") is False
    assert upd.check_newer("1.2.9", "1.3.0") is False
    assert upd.check_newer("v1.3.5", "1.3.4") is True


# ---------- 下载地址安全闸门 ----------

def test_download_update_rejects_non_github(tmp_path):
    path, err = upd.download_update(
        "https://evil.example.com/ShudaoLe-1.0.0-setup.exe", "x.exe",
        dest_dir=tmp_path)
    assert path == ""
    assert "GitHub" in err


def test_download_update_rejects_http(tmp_path):
    path, err = upd.download_update(
        "http://github.com/tau625/ShudaoLe/releases/download/v1/x.exe",
        "x.exe", dest_dir=tmp_path)
    assert path == ""
    assert "GitHub" in err


# ---------- 镜像加速（v1.4.0 默认启用） ----------

def test_mirror_prefixes_default_builtin(monkeypatch):
    for var in (upd.MIRROR_ENV, upd.NO_MIRROR_ENV):
        monkeypatch.delenv(var, raising=False)
    assert upd.mirror_prefixes() == list(upd.DEFAULT_MIRRORS)


def test_mirror_prefixes_disabled(monkeypatch):
    monkeypatch.setenv(upd.NO_MIRROR_ENV, "1")
    assert upd.mirror_prefixes() == []


def test_mirror_prefixes_custom_overrides_builtin(monkeypatch):
    monkeypatch.setenv(upd.MIRROR_ENV, "https://my.mirror.example/")
    assert upd.mirror_prefixes() == ["https://my.mirror.example"]


def test_candidate_urls_mirror_first_direct_last():
    url = "https://github.com/tau625/ShudaoLe/releases/download/v1/x.exe"
    urls = upd._candidate_urls(url)
    assert urls[-1] == url                      # 直连永远兜底
    assert urls[0].startswith("https://")       # 镜像在前
    assert urls[0].endswith(url)                # 前缀式拼接


def test_download_update_tries_mirror_then_direct(tmp_path, monkeypatch):
    """镜像失败后必须继续尝试直连，且错误信息包含每次尝试。"""
    calls = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b""

    def fake_open(self, req, timeout=0):
        calls.append(req.full_url)
        if "mirror" in req.full_url:
            raise OSError("mirror down")
        raise OSError("direct down")

    import urllib.request as _ur
    monkeypatch.setattr(_ur.OpenerDirector, "open", fake_open)
    path, err = upd.download_update(
        "https://github.com/tau625/ShudaoLe/releases/download/v1/x.exe",
        "x.exe", dest_dir=tmp_path)
    assert path == ""
    assert len(calls) == len(upd.mirror_prefixes()) + 1   # 镜像全部试过 + 直连
    assert calls[-1].startswith("https://github.com/")    # 直连是最后一个
    assert err.count("下载失败") >= 2


# ---------- 安装器启动参数 ----------

def test_start_windows_installer_missing_file(monkeypatch):
    monkeypatch.setattr(upd.os.path, "isfile", lambda p: False)
    ok, err = upd.start_windows_installer("C:\\nonexistent.exe")
    assert ok is False
    assert "不存在" in err


def test_start_windows_installer_flags(monkeypatch, tmp_path):
    """静默与强杀要保留，但不能吞错误框、不能与 [Run] 重复启动新版。"""
    exe = tmp_path / "ShudaoLe-9.9.9-setup.exe"
    exe.write_bytes(b"")
    captured = {}

    class _Popen:
        def __init__(self, args, **kw):
            captured["args"] = args

    monkeypatch.setattr(upd.subprocess, "Popen", _Popen)
    ok, err = upd.start_windows_installer(str(exe))
    assert ok is True and err == ""
    flags = captured["args"][1:]
    assert "/SILENT" in flags
    assert "/FORCECLOSEAPPLICATIONS" in flags
    # 吞掉 Inno 错误框 -> 安装失败无痕，正是「装了没装上」最难查的一环
    assert "/SUPPRESSMSGBOXES" not in flags
    # 与 installer.iss [Run] 重复，会让新版本被启动两次
    assert "/RESTARTAPPLICATIONS" not in flags


def test_start_windows_installer_reports_uac_rejection(monkeypatch, tmp_path):
    """UAC 取消时 CreateProcess 直接失败，必须如实报错，不能假装已启动。"""
    exe = tmp_path / "ShudaoLe-9.9.9-setup.exe"
    exe.write_bytes(b"")

    def _boom(args, **kw):
        raise OSError("需要提升权限")

    monkeypatch.setattr(upd.subprocess, "Popen", _boom)
    ok, err = upd.start_windows_installer(str(exe))
    assert ok is False
    assert "UAC" in err


# ---------- 下载就绪后自动安装（v1.3.8 修复） ----------

def _run_update_worker(monkeypatch, tmp_path, running):
    """跑一次真实的 _update_worker，返回 update_install 被调用次数。"""
    from shudaole import update as upd_mod
    from shudaole.gui import server as srv

    calls = {"install": 0}
    monkeypatch.setattr(srv, "add_log", lambda *a, **k: None)
    monkeypatch.setattr(srv, "update_install",
                        lambda: (calls.__setitem__("install", calls["install"] + 1), (True, ""))[1])
    monkeypatch.setattr(upd_mod, "check_latest", lambda *a, **k: {
        "latest": "9.9.9", "zip_url": "https://github.com/tau625/ShudaoLe/x",
        "asset_name": "ShudaoLe-9.9.9-setup.exe", "assets": []})
    monkeypatch.setattr(upd_mod, "download_update",
                        lambda *a, **k: (str(tmp_path / "ShudaoLe-9.9.9-setup.exe"), ""))
    monkeypatch.setattr(upd_mod, "_checksums_for", lambda *a, **k: None)
    monkeypatch.setattr("shudaole.config.config_dir", lambda: tmp_path)

    prev = srv.STATE["running"]
    srv.STATE["running"] = running
    try:
        srv._update_worker()
    finally:
        srv.STATE["running"] = prev
        srv.UPDATER_STATE.update(phase="idle", error="")
    return calls["install"], srv.UPDATER_STATE["kind"]


def test_worker_installs_automatically_when_ready(monkeypatch, tmp_path):
    """「一键更新」= 下载 + 安装；停在 ready 等第二次点击就是 bug。"""
    n, kind = _run_update_worker(monkeypatch, tmp_path, running=False)
    assert kind == "installer"
    assert n == 1


def test_worker_waits_when_download_task_running(monkeypatch, tmp_path):
    """教材下载任务在跑时不能装（安装器会强杀进程中断任务），停在 ready。"""
    n, kind = _run_update_worker(monkeypatch, tmp_path, running=True)
    assert kind == "installer"
    assert n == 0

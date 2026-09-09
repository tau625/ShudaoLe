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


# ---------- 安装器启动参数 ----------

def test_start_windows_installer_missing_file(monkeypatch):
    monkeypatch.setattr(upd.os.path, "isfile", lambda p: False)
    ok, err = upd.start_windows_installer("C:\\nonexistent.exe")
    assert ok is False
    assert "不存在" in err

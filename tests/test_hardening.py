# -*- coding: utf-8 -*-
"""2026-09-09 全项目体检修复的回归测试（P0-1/2，P1-3/4，P2-12）。"""
import threading
from urllib.parse import urlparse

import pytest

from shudaole import download as dl
import shudaole.net as net_mod
from shudaole.download import AuthContext, download_many
from shudaole.errors import DownloadError
from shudaole.net import get_with_redirect_check
from shudaole.naming import sanitize_filename
from shudaole.download import _NameRegistry


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch):
    """mock 环境无真实 DNS：把 SSRF 校验换成本地判定版（只拦本机/私有语义）。"""
    def fake_validate(url):
        host = (urlparse(url).hostname or "")
        if host in ("127.0.0.1", "::1", "localhost") or \
                host.endswith((".local", ".internal")):
            raise DownloadError(f"拒绝请求本地主机名: {host}")
        return url
    monkeypatch.setattr(net_mod, "validate_public_http_url", fake_validate)


# ---------- P2-12：Windows 保留设备名 ----------
@pytest.mark.parametrize("name", ["CON", "con", "Nul", "COM1", "lpt9",
                                  "CON.pdf", "aux.pdf"])
def test_sanitize_filename_reserved_devices(name):
    out = sanitize_filename(name)
    assert out.startswith("_"), out


@pytest.mark.parametrize("name", ["数学", "note.pdf", "小学语文_人教版"])
def test_sanitize_filename_normal_names_untouched(name):
    assert sanitize_filename(name) == name


# ---------- P1-3：令牌脱敏 ----------
def test_redact_token_removes_known_token():
    msg = "下载失败: 500 Server Error for url: https://r1/x.pdf?accessToken=SECRET123&a=1"
    out = dl.redact_token(msg, "SECRET123")
    assert "SECRET123" not in out
    assert "accessToken=***" in out


def test_redact_token_fallback_pattern():
    # token 变量对不上时，兜底正则也要能抹掉
    msg = "error for https://r1/x.pdf?foo=1&accessToken=LEAK&x=2"
    out = dl.redact_token(msg, None)
    assert "LEAK" not in out
    assert "accessToken=***" in out


def test_redact_token_plain_message_untouched():
    assert dl.redact_token("HTTP 500（服务端临时故障）", "SECRET") == \
        "HTTP 500（服务端临时故障）"


# ---------- P0-2：并发同名名册 ----------
def test_name_registry_same_base_gets_distinct_names(tmp_path):
    reg = _NameRegistry()
    d1, skip1 = reg.resolve(tmp_path, "同一本书")
    d2, skip2 = reg.resolve(tmp_path, "同一本书")
    assert not skip1 and not skip2
    assert d1 != d2
    assert d1.name == "同一本书.pdf"
    assert d2.name == "同一本书(2).pdf"


def test_name_registry_respects_existing_file(tmp_path):
    big = tmp_path / "已有书.pdf"
    big.write_bytes(b"x" * 1_100_000)
    reg = _NameRegistry()
    _, skip = reg.resolve(tmp_path, "已有书")
    assert skip is True


def test_download_many_same_title_concurrent_no_clash(requests_mock, tmp_path):
    """两个不同 contentId 解析出相同文件名（同名教材）时，并发下载
    不得写同一个 .part：两份都应成功且落成两个不同的文件。"""
    detail_tmpl = ("https://s-file-1.ykt.cbern.com.cn/zxx/ndrv2/resources/"
                   "tch_material/details/{cid}.json")
    pdf_tmpl = ("https://r1-ndr.ykt.cbern.com.cn/edu_product/esp/"
                "assets_document/{cid}.pkg/pdf.pdf")
    pdf_body = b"%PDF-1.7\n" + b"0" * 1200 + b"\n%%EOF"
    cids = ["11110000-0000-4000-8000-000000000000",
            "22220000-0000-4000-8000-000000000000"]
    for cid in cids:
        requests_mock.get(detail_tmpl.format(cid=cid), json={
            "id": cid, "title": "完全同名教材",
            "ti_items": [{"ti_storages": [pdf_tmpl.format(cid=cid)]}],
            "tag_list": [{"tag_dimension_id": "zxxxd", "tag_name": "小学"},
                         {"tag_dimension_id": "zxxxk", "tag_name": "语文"}],
        })
        requests_mock.get(pdf_tmpl.format(cid=cid), content=pdf_body)
    entries = ["https://basic.smartedu.cn/tchMaterial/detail"
               "?contentType=assets_document&contentId=%s" % c for c in cids]
    results = download_many(entries, tmp_path, AuthContext(None, False, False),
                            timeout=5, workers=2)
    assert [r["status"] for r in results] == ["ok", "ok"]
    pdfs = {p.name for p in tmp_path.glob("*.pdf")}
    assert len(pdfs) == 2
    # build_filename 产出 base = 小学语文_完全同名教材（结构段 + 区分语），
    # 同名后来者自动挪 (2)，两个 worker 各得一个独立文件
    assert pdfs == {"小学语文_完全同名教材.pdf", "小学语文_完全同名教材(2).pdf"}


# ---------- P1-4：重定向逐跳复检 ----------
def test_get_with_redirect_check_blocks_private_hop(requests_mock):
    # 首跳是公网域名，302 跳到环回地址——必须被 SSRF 闸门拦下
    requests_mock.get("https://evil-cdn.example.com/a.pdf",
                      status_code=302,
                      headers={"Location": "http://127.0.0.1:9/x.pdf"})
    import requests
    session = requests.Session()
    with pytest.raises(DownloadError):
        get_with_redirect_check(session, "https://evil-cdn.example.com/a.pdf")


def test_get_with_redirect_check_allows_normal_hop(requests_mock):
    # 正常镜像跳转：公网 -> 公网，应跟随并返回最终响应
    requests_mock.get("https://a.example.com/a.pdf", status_code=302,
                      headers={"Location": "https://b.example.com/a.pdf"})
    requests_mock.get("https://b.example.com/a.pdf",
                      content=b"%PDF-ok", status_code=200)
    import requests
    session = requests.Session()
    resp = get_with_redirect_check(session, "https://a.example.com/a.pdf")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-ok"


def test_get_with_redirect_check_hop_limit(requests_mock):
    # 无限循环重定向：超过跳数上限必须中止而不是永远跟
    requests_mock.get("https://loop.example.com/a.pdf", status_code=302,
                      headers={"Location": "https://loop.example.com/a.pdf"})
    import requests
    session = requests.Session()
    with pytest.raises(DownloadError):
        get_with_redirect_check(session, "https://loop.example.com/a.pdf")


# ---------- 名册线程安全冒烟 ----------
def test_name_registry_threaded_claim(tmp_path):
    reg = _NameRegistry()
    names = []
    gate = threading.Barrier(8)

    def worker():
        gate.wait()
        d, skip = reg.resolve(tmp_path, "热点书")
        assert not skip
        names.append(d.name)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(names)) == 8


# ---------- 2026-09-10：路径穿越 / SSRF 加固（安全扫描高危项回归） ----------
def test_token_save_path_rejects_traversal_base(tmp_path, monkeypatch):
    """base_dir 带「..」上跳成分或空字节时拒绝兜底目录，回落配置目录。"""
    import os as _os
    from shudaole import token as token_mod
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("SHUDAOLE_CONFIG_DIR", str(cfg_dir))
    evil = str(tmp_path / "out" / ".." / ".." / "elsewhere")
    assert token_mod.token_save_path(evil) == str(cfg_dir / "token.txt")
    assert token_mod.token_save_path("with\x00nul") == str(cfg_dir / "token.txt")
    # 合法程序目录：配置目录探测失败（不可写）时才走兜底，返回 base/token.txt
    real_makedirs = _os.makedirs

    def denied_makedirs(path, *a, **k):
        if str(path) == str(cfg_dir):
            raise OSError("denied for test")
        return real_makedirs(path, *a, **k)

    monkeypatch.setattr(_os, "makedirs", denied_makedirs)
    good = tmp_path / "prog"
    good.mkdir()
    assert token_mod.token_save_path(str(good)) == str(good / "token.txt")


def test_read_token_ignores_traversal_base(tmp_path, monkeypatch):
    from shudaole import token as token_mod
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "token.txt").write_text("T0KEN", encoding="utf-8")
    monkeypatch.setenv("SHUDAOLE_CONFIG_DIR", str(cfg_dir))
    evil = str(tmp_path / ".." / ".." / "elsewhere")
    assert token_mod.read_token(evil) == "T0KEN"


def test_download_update_rejects_bad_asset_name(tmp_path):
    """asset_name 来自远端元数据：带分隔符/盘符/上跳成分的名字一律拒绝且不发起网络请求。"""
    from shudaole import update as up
    url = ("https://github.com/tau625/ShudaoLe/releases/download/v1.4.1/"
           "ShudaoLe-1.4.1-setup.exe")
    for bad in ("..\\..\\evil.exe", "../evil.exe", "/abs/path.exe", r"C:\evil.exe",
                "..", "", "ShudaoLe-1.4.1-setup.exe\n"):
        out, err = up.download_update(url, bad, dest_dir=tmp_path)
        assert out == "", (bad, out)
        assert "不合规" in err, (bad, err)
    assert list(tmp_path.iterdir()) == []   # 未落任何文件


def test_download_update_rejects_non_github_url(tmp_path):
    from shudaole import update as up
    out, err = up.download_update(
        "https://evil.example.com/ShudaoLe-1.4.1-setup.exe",
        "ShudaoLe-1.4.1-setup.exe", dest_dir=tmp_path)
    assert out == "" and "GitHub" in err


def _load_gen_manifests():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "packaging" / "winget" / "gen_manifests.py"
    spec = importlib.util.spec_from_file_location("gen_manifests", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gen_manifests_safe_version_rejects_traversal():
    gm = _load_gen_manifests()
    with pytest.raises(SystemExit):
        gm._safe_version("../../evil")
    with pytest.raises(SystemExit):
        gm._safe_version("1.4.1 extra")
    assert gm._safe_version("1.4.1") == "1.4.1"


def test_gen_manifests_assert_safe_url_blocks_non_github():
    gm = _load_gen_manifests()
    for bad in ("file:///c:/windows/system32",
                "http://127.0.0.1:8765/x",
                "http://localhost/x",
                "https://192.168.1.10/x",
                "https://evil.example.com/x"):
        with pytest.raises(SystemExit):
            gm._assert_safe_url(bad)
    gm._assert_safe_url(
        "https://github.com/tau625/ShudaoLe/releases/download/v1.4.1/SHA256SUMS.txt")
    gm._assert_safe_url("https://objects.githubusercontent.com/x")

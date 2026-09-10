# -*- coding: utf-8 -*-
"""下载内核测试：令牌优先级、PDF 校验、详情获取（requests-mock，无真实网络）。"""
import pytest
import requests
import requests_mock as rm_module

from shudaole import download
from shudaole.download import (
    fetch_detail, initial_token, verify_pdf, DETAIL_ENDPOINTS,
    AuthContext, process_one,
)
from shudaole.errors import DetailFetchError, DownloadError, NeedAuthError


@pytest.fixture
def requests_mock():
    with rm_module.Mocker() as m:
        yield m


@pytest.fixture
def _skip_dns(monkeypatch):
    """mock 环境无真实 DNS：SSRF 校验放行为公网测试域名（打在 shudaole.net 上，
    download.get_with_redirect_check 引用的是 net 模块全局）。"""
    import shudaole.net as net_mod
    monkeypatch.setattr(net_mod, "validate_public_http_url", lambda url: url)


# ---------- initial_token：优先级链 ----------

def test_initial_token_param_first(monkeypatch, tmp_path):
    monkeypatch.setattr(download, "TOKEN_FILE", tmp_path / "a.txt")
    monkeypatch.setattr(download, "TOKEN_FILE_LEGACY", tmp_path / "b.txt")
    monkeypatch.setenv("SMARTEDU_TOKEN", "env-token")
    assert initial_token("param-token") == "param-token"


def test_initial_token_env_second(monkeypatch, tmp_path):
    monkeypatch.setattr(download, "TOKEN_FILE", tmp_path / "a.txt")
    monkeypatch.setattr(download, "TOKEN_FILE_LEGACY", tmp_path / "b.txt")
    monkeypatch.setenv("SMARTEDU_TOKEN", "env-token")
    assert initial_token(None) == "env-token"
    monkeypatch.setenv("SMARTEDU_TOKEN", "  ")  # 空白视同未设置
    assert initial_token(None) is None


def test_initial_token_config_file_then_legacy(monkeypatch, tmp_path):
    new = tmp_path / "new.txt"
    old = tmp_path / "old.txt"
    monkeypatch.setattr(download, "TOKEN_FILE", new)
    monkeypatch.setattr(download, "TOKEN_FILE_LEGACY", old)

    old.write_text("legacy-token", encoding="utf-8")
    assert initial_token(None) == "legacy-token"

    new.write_text("config-token\n", encoding="utf-8")  # 配置目录优先于旧位置
    assert initial_token(None) == "config-token"


# ---------- verify_pdf ----------

def _make_pdf(path):
    # verify_pdf 要求 ≥1024 字节、%PDF 头、尾部 %%EOF
    body = b"%PDF-1.7\n" + b"%" + b"0" * 1200 + b"\n%%EOF"
    path.write_bytes(body)
    return path


def test_verify_pdf_ok(tmp_path):
    p = _make_pdf(tmp_path / "a.pdf")
    ok, reason = verify_pdf(p)
    assert ok is True and reason == ""


def test_verify_pdf_garbage(tmp_path):
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"<!DOCTYPE html>" + b"x" * 1100)
    ok, reason = verify_pdf(p)
    assert ok is False and reason


def test_verify_pdf_too_small(tmp_path):
    p = tmp_path / "tiny.pdf"
    p.write_bytes(b"%PDF")
    ok, reason = verify_pdf(p)
    assert ok is False and "过小" in reason


def test_verify_pdf_truncated(tmp_path):
    p = tmp_path / "cut.pdf"
    p.write_bytes(b"%PDF-1.7\n" + b"0" * 1200)  # 无 %%EOF
    ok, reason = verify_pdf(p)
    assert ok is False and "EOF" in reason


# ---------- fetch_detail（requests-mock 全拦截） ----------

def test_fetch_detail_success(requests_mock, sample_detail):
    url = DETAIL_ENDPOINTS[0].format(cid="abc123")
    requests_mock.get(url, json=sample_detail)
    session = __import__("shudaole.net", fromlist=["build_session"]).build_session()
    data = fetch_detail("abc123", session, timeout=5)
    assert data["title"] == sample_detail["title"]


def test_fetch_detail_403_all_endpoints(requests_mock):
    for tmpl in DETAIL_ENDPOINTS:
        requests_mock.get(tmpl.format(cid="x"), status_code=403)
    session = __import__("shudaole.net", fromlist=["build_session"]).build_session()
    with pytest.raises(DetailFetchError):
        fetch_detail("x", session, timeout=5)


def test_fetch_detail_missing_fields(requests_mock):
    for tmpl in DETAIL_ENDPOINTS:
        requests_mock.get(tmpl.format(cid="y"), json={"foo": 1})
    session = __import__("shudaole.net", fromlist=["build_session"]).build_session()
    with pytest.raises(DetailFetchError):
        fetch_detail("y", session, timeout=5)


# ---------- download_file：4xx 中止候选轮换（2026-09-10 回归锁） ----------

PDF_BODY = b"%PDF-1.7\n" + b"0" * 1200 + b"\n%%EOF"


def test_download_file_rotates_candidates_on_404(requests_mock, _skip_dns, tmp_path):
    """第一个候选 404 时不得冲出循环：必须换第二个候选并成功落盘。

    回归背景：非 200/206 曾直接 raise DownloadError 且无 except 捕获，
    r1/r2/r3 镜像、-private 变体、固定模板兜底全部不再尝试。"""
    requests_mock.get("https://r1-ndr.example.com/a.pdf", status_code=404)
    requests_mock.get("https://r2-ndr.example.com/a.pdf", content=PDF_BODY)
    dest = tmp_path / "out.pdf"
    ok = download.download_file(
        ["https://r1-ndr.example.com/a.pdf", "https://r2-ndr.example.com/a.pdf"],
        dest, requests.Session(), retries=1, timeout=5)
    assert ok is True
    assert dest.read_bytes() == PDF_BODY


def test_download_file_all_404_410_appends_guidance(requests_mock, _skip_dns, tmp_path):
    """全部候选都 404/410：最终错误附「下架/改版」核对指引。"""
    requests_mock.get("https://r1-ndr.example.com/gone.pdf", status_code=404)
    requests_mock.get("https://r2-ndr.example.com/gone.pdf", status_code=410)
    with pytest.raises(DownloadError) as ei:
        download.download_file(
            ["https://r1-ndr.example.com/gone.pdf",
             "https://r2-ndr.example.com/gone.pdf"],
            tmp_path / "gone.pdf", requests.Session(), retries=1, timeout=5)
    msg = str(ei.value)
    assert msg.startswith("HTTP 4")          # 聚合的是最后一个候选的状态码
    assert "下架" in msg and "basic.smartedu.cn" in msg


def test_download_file_4xx_then_server_err_no_guidance(requests_mock, _skip_dns, tmp_path):
    """失败原因混杂（404 + 5xx）时不给下架指引，避免误导。"""
    requests_mock.get("https://r1-ndr.example.com/m.pdf", status_code=404)
    requests_mock.get("https://r2-ndr.example.com/m.pdf", status_code=503)
    with pytest.raises(DownloadError) as ei:
        download.download_file(
            ["https://r1-ndr.example.com/m.pdf", "https://r2-ndr.example.com/m.pdf"],
            tmp_path / "m.pdf", requests.Session(), retries=1, timeout=5)
    assert "下架" not in str(ei.value)


def test_download_file_malformed_length_header(requests_mock, _skip_dns, tmp_path):
    """畸形 Content-Length 头：int() 不再裸抛，按未知长度下载并靠 PDF 校验收尾。"""
    requests_mock.get("https://r1-ndr.example.com/bad.pdf", content=PDF_BODY,
                      headers={"Content-Length": "not-a-number"})
    dest = tmp_path / "bad.pdf"
    ok = download.download_file(["https://r1-ndr.example.com/bad.pdf"],
                                dest, requests.Session(), retries=1, timeout=5)
    assert ok is True
    assert dest.read_bytes() == PDF_BODY


# ---------- 401 降级链 ----------

def test_download_file_401_raises_need_auth(requests_mock, _skip_dns, tmp_path):
    requests_mock.get("https://r1-ndr.example.com/y.pdf", status_code=401)
    with pytest.raises(NeedAuthError):
        download.download_file(["https://r1-ndr.example.com/y.pdf"],
                               tmp_path / "y.pdf", requests.Session(),
                               retries=1, timeout=5)


def test_process_one_401_retry_chain_succeeds_without_token_leak(
        requests_mock, _skip_dns, tmp_path):
    """完整 401 降级链：下载 401 -> obtain_token -> 携带新令牌重试成功，
    且结果消息中不出现令牌明文。

    （直接跑 process_one 而非只测单环：obtain_token 以实例属性打桩，
    无需真实浏览器/交互输入。）"""
    cid = "11111111-2222-3333-4444-555555555555"
    detail_url = DETAIL_ENDPOINTS[0].format(cid=cid)
    pdf_url = "https://r1-ndr.example.com/chain.pdf"
    requests_mock.get(detail_url, json={
        "id": cid, "title": "鉴权重试测试教材",
        "ti_items": [{"ti_storages": [pdf_url]}],
        "tag_list": [],
    })
    # 同一 URL 第一次 401、第二次（带新令牌的会话）200
    requests_mock.get(pdf_url, [
        {"status_code": 401},
        {"status_code": 200, "content": PDF_BODY},
    ])
    auth = AuthContext(None, save_token=False, interactive=True)
    token = "SECRET-X-ND-AUTH-VALUE"
    auth.obtain_token = lambda on_log=None: token  # 打桩令牌发放（实例属性遮蔽）
    entry = ("https://basic.smartedu.cn/tchMaterial/detail"
             "?contentType=assets_document&contentId=" + cid)
    res = process_one(entry, tmp_path, auth, retries=1, timeout=5, flat_name=True)
    assert res["status"] == "ok", res["msg"]
    # 令牌明文不得出现在结果消息里（PDF 为二进制，泄漏面只可能是消息文本）
    assert token not in res["msg"]

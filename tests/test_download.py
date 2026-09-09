# -*- coding: utf-8 -*-
"""下载内核测试：令牌优先级、PDF 校验、详情获取（requests-mock，无真实网络）。"""
import pytest
import requests_mock as rm_module

from shudaole import download
from shudaole.download import (
    fetch_detail, initial_token, verify_pdf, DETAIL_ENDPOINTS,
)
from shudaole.errors import DetailFetchError


@pytest.fixture
def requests_mock():
    with rm_module.Mocker() as m:
        yield m


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

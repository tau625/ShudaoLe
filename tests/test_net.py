# -*- coding: utf-8 -*-
"""网络层测试：SSRF 校验（mock DNS，不发起真实请求）。"""
import socket

import pytest

from shudaole.errors import DownloadError
from shudaole.net import validate_public_http_url


def _patch_dns(monkeypatch, host, ip):
    infos = [(socket.AF_INET, 1, 6, "", (ip, 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **k: infos if h == host else [])


def test_reject_non_http_scheme():
    with pytest.raises(DownloadError):
        validate_public_http_url("ftp://example.com/x")
    with pytest.raises(DownloadError):
        validate_public_http_url("file:///etc/passwd")


def test_reject_local_hostname(monkeypatch):
    # 本地主机名在 DNS 解析前就应被拒绝
    with pytest.raises(DownloadError):
        validate_public_http_url("http://localhost/x")
    with pytest.raises(DownloadError):
        validate_public_http_url("http://foo.internal/x")


def test_reject_private_ip(monkeypatch):
    _patch_dns(monkeypatch, "evil.example.com", "192.168.1.10")
    with pytest.raises(DownloadError):
        validate_public_http_url("http://evil.example.com/x")


def test_reject_loopback_and_linklocal(monkeypatch):
    _patch_dns(monkeypatch, "loop.example.com", "127.0.0.1")
    with pytest.raises(DownloadError):
        validate_public_http_url("http://loop.example.com/x")
    _patch_dns(monkeypatch, "link.example.com", "169.254.1.1")
    with pytest.raises(DownloadError):
        validate_public_http_url("http://link.example.com/x")


def test_pass_public_ip(monkeypatch):
    ip = "39.156.66.10"
    _patch_dns(monkeypatch, "cdn.example.com", ip)
    url = "http://cdn.example.com/x.json"
    assert validate_public_http_url(url) == url


def test_dns_failure(monkeypatch):
    def boom(host, port, *a, **k):
        raise socket.gaierror("nxdomain")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(DownloadError):
        validate_public_http_url("http://nxdomain.example.com/x")

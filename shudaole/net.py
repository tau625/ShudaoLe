# -*- coding: utf-8 -*-
"""网络层：Session 构造（浏览器请求头/连接级重试）与 URL 安全校验。"""

import ipaddress
import socket
import sys
from urllib.parse import urlparse

from .errors import DownloadError

try:
    import requests
    from requests.adapters import HTTPAdapter
except ImportError:
    print("缺少 requests 库，请先执行: pip install requests")
    sys.exit(1)

try:
    from urllib3.util.retry import Retry
except ImportError:  # 极旧环境兜底：不挂连接级重试
    Retry = None

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")

# 教材详情 JSON（静态 CDN，无需登录；多端点互为镜像，逐个尝试）

# ---------- 会话与请求 ----------
def build_session(token=None):
    """构造带浏览器请求头的 Session（可选携带 X-ND-AUTH 登录令牌）"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://basic.smartedu.cn/",
        "Origin": "https://basic.smartedu.cn",
    })
    if token:
        session.headers["X-ND-AUTH"] = token
    if Retry is not None:
        # 连接级重试（网络抖动）；HTTP 语义层面的重试由本工具自己控制
        adapter = HTTPAdapter(max_retries=Retry(total=2, backoff_factor=0.5,
                                                status_forcelist=(500, 502, 503, 504)))
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    return session



def validate_public_http_url(url):
    """SSRF 防护：出站请求只放行 http/https，且 host 不得是本地/环回/
    私有/保留地址（按域名解析出的全部 IP 判定）。请求地址可能来自远端
    数据或用户粘贴的链接，统一过这道闸再真正发起请求。"""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise DownloadError(f"拒绝请求非法 URL: {url!r}")
    host = parts.hostname
    if host == "localhost" or host.endswith((".local", ".internal", ".home.arpa")):
        raise DownloadError(f"拒绝请求本地主机名: {host}")
    try:
        addr_infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise DownloadError(f"域名解析失败: {host}（{e}）") from e
    for info in addr_infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise DownloadError(f"拒绝请求解析到非公网地址的主机: {host} -> {ip}")
    return url



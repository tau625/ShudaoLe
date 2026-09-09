#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_fetch_token.py —— "一键登录获取令牌" 的纯 Python 后端抓取脚本
====================================================================

职责：用系统已装的 Edge/Chrome 打开一个真实教材详情页（有头、可见），用户若未登录
则在弹出的窗口里扫码/手机号登录；登录后详情页会自动加载 PDF.js 在线阅读器并请求
私有 PDF 资源（rX-ndr-private 域名），该请求会自动附带 x-nd-auth。本脚本通过
Chrome DevTools Protocol（CDP）监听整个浏览器会话里所有请求头，捕获第一个稳定的
x-nd-auth，写入令牌文件并输出 JSON 后退出。

与旧版 Node 实现的差异：
  - 不再依赖 Node.js / playwright-core，纯 Python 标准库（websocket 用底层 socket 手写
    RFC6455 帧，不引入任何第三方依赖），随 exe 一起打包后零额外运行时。
  - 浏览器仍用系统 Edge/Chrome（CDP remote-debugging 启动），无需下载浏览器二进制。

用法：
  python auto_fetch_token.py [--trigger-content <contentId>] [--token-file <path>]
                             [--timeout <秒>] [--login-url <url>]

成功退出码 0，输出: {"ok":true,"token":"...","source":"..."}
超时/失败退出码非 0，输出: {"ok":false,"error":"..."}

约定：本脚本既可作为独立 CLI 运行，也可被 GUI 直接 import 调用
      `run_token_fetch(...)`（在 GUI 进程内线程中运行，无需 subprocess 调外部
      Python，打包成 exe 后即零依赖）。CLI 模式 stdout 逐行输出 JSON
      （waiting/ok/fail 三类）；import 模式通过 emit 回调实时上报，两者协议一致。
"""

import argparse
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import shutil
from pathlib import Path

try:
    from .config import path_is_relative_to
except ImportError:  # 兜底（本模块不应脱离包单独运行，仅保险）
    def path_is_relative_to(path, base):
        try:
            Path(path).resolve().relative_to(Path(base).resolve())
            return True
        except ValueError:
            return False


# ============ 常量 ============
DEFAULT_TRIGGER_CID = "bdc00134-465d-454b-a541-dcd0cec4d86e"
LOGIN_HOST_HINTS = ("sso.", "auth.smartedu")
TRIGGER_URL_TPL = (
    "https://basic.smartedu.cn/tchMaterial/detail?contentType=assets_document"
    "&contentId={cid}&catalogType=tchMaterial&subCatalog=tchMaterial"
)


def _dbg(msg):
    """诊断日志：只在 stderr 存在时输出（windowed exe 下 sys.stderr 为 None）。"""
    try:
        if sys.stderr:
            sys.stderr.write("[shudaole] " + str(msg) + "\n")
    except Exception:
        pass


def _out(obj):
    """向 stdout 输出一行 JSON（供 GUI 解析）；管道被关闭（EPIPE）时静默忽略"""
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except OSError:
        pass


# ============ 浏览器可执行文件定位 ============
def _edge_candidates():
    env = os.environ.get("AUTO_EDGE", "").strip()
    if env:
        yield env
    if sys.platform == "darwin":
        yield "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
        yield "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        return
    if os.name != "nt":
        # Linux：包管理器（PATH 中）与 snap 的常见安装位置
        for name in ("microsoft-edge", "microsoft-edge-stable",
                     "google-chrome", "google-chrome-stable",
                     "chromium", "chromium-browser"):
            p = shutil.which(name)
            if p:
                yield p
        yield "/snap/bin/chromium"
        yield "/snap/bin/microsoft-edge"
        return
    yield r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    yield r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    yield r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    yield r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"


def find_browser():
    for c in _edge_candidates():
        if c and os.path.exists(c):
            return c
    return None


# ============ 用户目录布局 ============
def config_dir():
    """用户级配置目录 ~/.config/shudaole/（Windows 下同样位于 %USERPROFILE%\\.config\\，
    跨平台行为一致且不写注册表）。可用 SHUDAOLE_CONFIG_DIR 覆盖（测试/绿色版场景）。"""
    override = os.environ.get("SHUDAOLE_CONFIG_DIR", "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".config", "shudaole")


def default_profile_dir():
    """一键登录浏览器的独立 profile 目录。"""
    return os.path.join(config_dir(), "token-profile")


_LEGACY_PROFILE = os.path.join(
    os.path.expanduser("~"), ".workbuddy", "smartedu-token-profile")


def _migrate_profile():
    """一次性迁移：把历史版本的 ~/.workbuddy/smartedu-token-profile 搬到新位置，
    保留已有登录态（免得用户重登一次）。旧目录不存在、或新目录已就位、或搬迁
    失败（权限/占用）都静默放行——失败只损失登录态，不影响功能。"""
    legacy = _LEGACY_PROFILE
    new = default_profile_dir()
    if os.environ.get("AUTO_PROFILE"):
        return  # 用户显式指定了目录，不掺和
    if not os.path.isdir(legacy) or os.path.isdir(new):
        return
    try:
        os.makedirs(os.path.dirname(new), exist_ok=True)
        shutil.move(legacy, new)
    except OSError as e:
        print(f"[shudaole] 迁移浏览器登录目录失败（将重新登录一次）: {e}",
              file=sys.stderr)


# ============ 令牌落盘（统一读写口，供 CLI / GUI / 自动抓取共用） ============
def token_save_path(base_dir=None):
    """令牌文件写入路径：优先用户配置目录 ~/.config/shudaole/token.txt。

    base_dir 仅用于兜底：配置目录不可写（极少见）时退回 base_dir/token.txt，
    保持旧版行为。读取方（smartedu_downloader.initial_token）按
    「配置目录 > 程序目录」顺序找，两处都能命中。
    """
    cfg = os.path.join(config_dir(), "token.txt")
    try:
        os.makedirs(config_dir(), exist_ok=True)
        # 探测可写：直接原子创建一个 0 字节文件（已存在则无副作用）
        with open(cfg, "a", encoding="utf-8"):
            pass
        return cfg
    except OSError as e:
        print(f"[shudaole] 配置目录不可用，令牌将存到程序目录: {e}", file=sys.stderr)
        if base_dir:
            return os.path.join(str(base_dir), "token.txt")
        return cfg


def write_token(token, base_dir=None):
    """把令牌写入 token_save_path，POSIX 下收紧到属主可读写（600）。
    返回实际写入路径；失败返回 None（不抛异常——令牌还能通过内存使用）。"""
    path = token_save_path(base_dir)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(token.strip() or "")
        if os.name != "nt":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return path
    except OSError as e:
        print(f"[shudaole] 令牌落盘失败: {e}", file=sys.stderr)
        return None


def read_token(base_dir=None):
    """读取已存令牌：配置目录优先，其次程序目录（历史位置）。
    返回去除首尾空白的令牌字符串；没有则返回 None。"""
    candidates = [os.path.join(config_dir(), "token.txt")]
    if base_dir:
        candidates.append(os.path.join(str(base_dir), "token.txt"))
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                t = f.read().strip()
            if t:
                return t
        except OSError:
            continue
    return None


# ============ 最小 WebSocket 客户端（RFC6455，纯标准库） ============
class _WS:
    """仅够 CDP 使用的最小 WebSocket 客户端：client 握手 + 文本帧收发 + close"""

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, host, port, path):
        self._sock = socket.create_connection((host, port), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(req.encode("ascii"))
        # 读握手响应头（读到 \r\n\r\n）
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise OSError("WebSocket 握手失败：连接被关闭")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise OSError(f"WebSocket 握手失败: {head.splitlines()[0].decode('latin1')}")
        self._rbuf = bytearray(rest)

    def _read_exact(self, n):
        while len(self._rbuf) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise OSError("WebSocket 连接关闭")
            self._rbuf.extend(chunk)
        data = bytes(self._rbuf[:n])
        del self._rbuf[:n]
        return data

    def recv(self):
        """读一条完整消息，返回文本载荷（str）；处理 close/ping/pong 与分片。

        RFC6455：一条消息可以拆成多个帧——首帧 opcode=0x1 且 fin=0，其后各帧为
        opcode=0x0（continuation），最后一帧 fin=1。旧实现丢弃 fin=0 的帧，
        遇到长 CDP 消息（体积大的 Network 事件）就会拿到半截 JSON。
        """
        frags = None       # 已收集的分片字节（None 表示尚未收到首帧）
        while True:
            hdr = self._read_exact(2)
            b0, b1 = hdr[0], hdr[1]
            fin = (b0 & 0x80) != 0
            opcode = b0 & 0x0F
            masked = (b1 & 0x80) != 0
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else None
            payload = self._read_exact(length) if length else b""
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:  # close
                raise OSError("WebSocket 已关闭")
            if opcode == 0x9:  # ping -> pong
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:  # pong（CDP 不发，收到即忽略）
                continue
            if opcode in (0x1, 0x0):  # text / continuation
                if opcode == 0x1:
                    frags = bytearray(payload)      # 新消息首帧
                elif frags is not None:
                    frags.extend(payload)           # 续帧追加
                # frags is None 的孤立续帧属协议异常，直接丢弃
                if fin and frags is not None:
                    return frags.decode("utf-8", "replace")
                continue
            continue  # 二进制等其它帧：CDP 不使用，忽略

    def _send_frame(self, opcode, payload):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        mask = os.urandom(4)
        n = len(payload)
        hdr = bytes([0x80 | opcode])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(hdr + mask + masked)

    def send(self, text):
        self._send_frame(0x1, text)

    def close(self):
        # 关闭时连接可能已被对端断开，OSError 属预期内
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


# ============ CDP 客户端 ============
class CDP:
    """极简 CDP 客户端：连接 /devtools/page/<id> 或 /devtools/browser/<id>，收发命令/事件"""

    def __init__(self, ws_url):
        self._id = 0
        self._pending = {}
        self._events = []  # [(method, params)]
        u = urllib.parse.urlsplit(ws_url)
        self._ws = _WS(u.hostname, u.port, u.path or "/")

    def send(self, method, params=None, timeout=15):
        self._id += 1
        mid = self._id
        msg = json.dumps({"id": mid, "method": method,
                          "params": params or {}})
        self._ws.send(msg)
        # 同步等待该 id 的响应，途中把事件丢进缓冲
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self._ws.recv()
            obj = json.loads(data)
            if obj.get("id") == mid:
                if "error" in obj:
                    raise RuntimeError(f"CDP {method}: {obj['error']}")
                return obj.get("result", {})
            if obj.get("method"):
                self._events.append((obj["method"], obj.get("params", {})))
        raise TimeoutError(f"CDP 命令超时: {method}")

    def poll_events(self):
        """非阻塞地把当前 socket 里已到达的事件读出来（依赖超时短的 recv）"""
        evs = []
        try:
            self._ws._sock.settimeout(0.05)
            while True:
                try:
                    data = self._ws.recv()
                except socket.timeout:
                    break
                except OSError:
                    break
                obj = json.loads(data)
                if obj.get("method"):
                    evs.append((obj["method"], obj.get("params", {})))
        finally:
            self._ws._sock.settimeout(15)
        return evs


# ============ 主流程 ============
def run_token_fetch(trigger_content=DEFAULT_TRIGGER_CID, token_file=None,
                    timeout=180, login_url="", emit=None, cancel_check=None):
    """核心抓取流程，可被 GUI 进程内直接调用（无需 subprocess 调外部 Python）。

    参数:
      trigger_content: 用于触发资源加载的真实教材 contentId
      token_file:      令牌写入路径（None 则不落盘，仅通过返回值带回）
      timeout:         最长等待秒数
      login_url:       覆盖起始页 URL（测试/特殊场景）
      emit:            回调 emit(obj)，用于实时上报状态（waiting/ok/fail），
                       obj 形如 {"ok": None, "waiting": True, "message": "..."}
                       / {"ok": True, "token": "..."} / {"ok": False, "error": "..."}
      cancel_check:    回调 cancel_check() -> bool，返回 True 表示用户已放弃，应立即退出
                       并清理浏览器。可为 None（表示不检查取消）。

    返回 (code, payload)：
      code 0 成功 / 3 超时 / 4 无浏览器 / 5 调试接口失败 / 6 连接断开 / 7 用户取消
      payload 为对应的 JSON 对象（与 emit 上报内容一致）。
    """
    if emit is None:
        emit = _out

    timeout = max(30, int(timeout))
    trigger_url = TRIGGER_URL_TPL.format(cid=urllib.parse.quote(str(trigger_content)))
    use_override = bool((login_url or "").strip())
    start_url = login_url.strip() if use_override else trigger_url

    exe = find_browser()
    if not exe:
        p = {"ok": False, "error": "未检测到 Edge/Chrome 浏览器，请手动获取并粘贴令牌"}
        emit(p)
        return 4, p

    # 独立持久化 profile：登录态留存，一次登录后续免登，不干扰日常浏览器
    # 存放于用户配置目录 ~/.config/shudaole/（历史版本曾用 ~/.workbuddy/，见 _migrate_profile）
    profile = os.environ.get("AUTO_PROFILE") or default_profile_dir()
    _migrate_profile()
    os.makedirs(profile, exist_ok=True)

    # 随机取一个高位端口做 remote-debugging
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    debug_port = probe.getsockname()[1]
    probe.close()

    cmd = [
        exe,
        f"--remote-debugging-port={debug_port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        start_url,
    ]
    creationflags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=creationflags)

    # 等待 CDP 端点就绪（读 /json/version 或 /json 列表）。
    # 浏览器进程退出不立即判死：启动加速/交接场景下 spawn 的根进程可能秒退而
    # 调试端口仍由交接后的实例服务，给 3 秒观察期。
    deadline = time.time() + 30
    ws_url = None
    exited_at = None
    while time.time() < deadline:
        if cancel_check and cancel_check():
            _kill(proc)
            p = {"ok": False, "error": "已放弃"}
            emit(p)
            return 7, p
        if proc.poll() is not None:
            if exited_at is None:
                exited_at = time.time()
            elif time.time() - exited_at > 3:
                break  # 进程确实退了且端口始终未就绪
        try:
            with socket.create_connection(("127.0.0.1", debug_port), timeout=2):
                pass
            ws_url = _fetch_ws_url(debug_port)
            if ws_url:
                break
        except OSError:
            pass
        time.sleep(0.4)

    if not ws_url:
        p = {"ok": False,
             "error": ("浏览器进程意外退出，无法启动调试会话"
                       if proc.poll() is not None else "等待浏览器调试接口超时")}
        emit(p)
        _kill(proc)
        return 5, p

    cdp = CDP(ws_url)
    # 开启网络与页面领域，抓取请求头
    # （CDP 命令可能抛 RuntimeError(协议错误)/TimeoutError/OSError；失败要留下线索，
    #  不再无脑 pass——否则「抓不到令牌」永远查不出是哪一步没起来）
    try:
        cdp.send("Network.enable")
    except (RuntimeError, TimeoutError, OSError) as e:
        _dbg(f"Network.enable 失败: {e}")
    try:
        cdp.send("Page.enable")
    except (RuntimeError, TimeoutError, OSError) as e:
        _dbg(f"Page.enable 失败: {e}")
    # 目标页若尚未加载，触发导航到起始页（通常启动参数已带 URL）
    try:
        cdp.send("Page.navigate", {"url": start_url})
    except (RuntimeError, TimeoutError, OSError) as e:
        _dbg(f"Page.navigate 失败: {e}")

    emit({"ok": None, "waiting": True, "message": "正在打开浏览器窗口，请稍候..."})

    seen = set()
    navigated_to_trigger = use_override
    last_wait_hint = [0.0]
    started = time.time()
    captured = None

    def try_capture(params):
        nonlocal captured
        try:
            headers = params.get("request", {}).get("headers", {})
            tok = (headers.get("x-nd-auth") or headers.get("X-ND-Auth") or "").strip()
            if not tok or len(tok) < 10 or tok in seen:
                return
            url = params.get("request", {}).get("url", "")
            # 只看对私有资源/主站资源发起的请求，过滤匿名占位
            if not ("-private" in url or "x-nd-auth" in url
                    or "ykt.cbern" in url or "basic.smartedu" in url):
                return
            seen.add(tok)
            captured = tok
        except (AttributeError, KeyError, TypeError):
            # 事件结构异常（缺字段/类型不符）跳过即可，日志不打——高频事件会刷屏
            pass

    while time.time() - started < timeout:
        if cancel_check and cancel_check():
            # 用户放弃：走快速强杀（不做 Browser.close 等待），保证取消即时生效
            _kill(proc)
            p = {"ok": False, "error": "已放弃"}
            emit(p)
            return 7, p
        try:
            events = cdp.poll_events()
            for method, params in events:
                if method in ("Network.requestWillBeSent", "Network.requestWillBeSentExtraInfo"):
                    try_capture(params)
                elif method == "Page.frameNavigated":
                    # 登录跳转回主站后，确保停在详情页触发阅读器
                    if not use_override and not captured:
                        pass
            if captured:
                break

            # 检查当前页面 URL，决定提示文案 / 是否需要导航回详情页
            if not use_override and not captured:
                cur = _page_url(cdp)
                if cur and ("sso." in cur or "auth.smartedu" in cur):
                    _hint("请在弹出的窗口登录 basic.smartedu.cn（手机号/扫码）",
                          last_wait_hint, emit)
                else:
                    if cur and "tchMaterial/detail" not in cur and not navigated_to_trigger:
                        navigated_to_trigger = True
                        emit({"ok": None, "waiting": True,
                              "message": "已打开教材详情页，正在触发资源加载并捕获令牌..."})
                        try:
                            cdp.send("Page.navigate", {"url": trigger_url})
                        except (RuntimeError, TimeoutError, OSError) as e:
                            _dbg(f"回跳教材详情页失败: {e}")
                    else:
                        _hint("正在触发资源加载并捕获令牌...", last_wait_hint, emit)
        except OSError:
            p = {"ok": False, "error": "与浏览器的连接已断开（窗口可能被关闭）"}
            emit(p)
            _kill(proc)
            return 6, p
        time.sleep(0.3)

    if captured:
        if token_file:
            # 优先写入用户配置目录（write_token 内部处理目录不存在/无权限的情况）；
            # 传入的 token_file 仅作为兜底目录使用，且仍做路径穿越防护——
            # 只取纯文件名拼回规范化后的目录，落盘前再校验包含关系。
            raw = Path(token_file)
            base = raw.resolve().parent
            target = base / raw.name
            if path_is_relative_to(target, base):
                write_token(captured, base_dir=base)
        p = {"ok": True, "token": captured,
             "source": "自动捕获于 " + time.strftime("%H:%M:%S")}
        emit(p)
        _shutdown_browser(debug_port, proc)
        return 0, p

    p = {"ok": False,
         "error": f"等待 {timeout} 秒仍未捕获到令牌（可能未登录或窗口被关闭）"}
    emit(p)
    _shutdown_browser(debug_port, proc)
    return 3, p


def main():
    ap = argparse.ArgumentParser(description="自动获取 smartedu x-nd-auth 令牌")
    ap.add_argument("--trigger-content", default=DEFAULT_TRIGGER_CID)
    ap.add_argument("--token-file", default=token_save_path(
        os.path.dirname(os.path.abspath(__file__))),
                    help="令牌文件位置（默认用户配置目录 ~/.config/shudaole/token.txt）")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--login-url", default="")
    args = ap.parse_args()

    code, _payload = run_token_fetch(
        trigger_content=args.trigger_content,
        token_file=args.token_file,
        timeout=args.timeout,
        login_url=args.login_url,
    )
    return code


def _hint(text, last_ref, emit=None):
    """节流输出 waiting 提示，避免刷屏。last_ref 为单元素列表以便原地更新。"""
    if emit is None:
        emit = _out
    now = time.time()
    if now - last_ref[0] > 3.0:
        emit({"ok": None, "waiting": True, "message": text})
        last_ref[0] = now


def _page_url(cdp):
    """取当前页面 URL；查询失败返回空串（调用方据此判断状态，不致命）"""
    try:
        res = cdp.send("Runtime.evaluate",
                       {"expression": "location.href", "returnByValue": True})
        return res.get("result", {}).get("value", "")
    except (RuntimeError, TimeoutError, OSError, KeyError) as e:
        _dbg(f"读取页面地址失败: {e}")
        return ""


def _fetch_ws_url(port):
    """从 CDP 端点取一个可用的 page websocket URL"""
    import http.client
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("GET", "/json/list")
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8", "replace"))
        conn.close()
        for t in data:
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                return t["webSocketDebuggerUrl"]
        # 无 page 时退回 browser 级
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("GET", "/json/version")
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8", "replace"))
        conn.close()
        return data.get("webSocketDebuggerUrl")
    except (OSError, ValueError) as e:
        # OSError: 调试端口还没起来/连接断开；ValueError: 返回的不是合法 JSON
        _dbg(f"读取 CDP 端点失败: {e}")
        return None


def _browser_ws(port):
    """取浏览器级（browser-level）webSocketDebuggerUrl，用于 Browser.close 优雅关闭"""
    import http.client
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("GET", "/json/version")
        data = json.loads(conn.getresponse().read().decode("utf-8", "replace"))
        conn.close()
        return data.get("webSocketDebuggerUrl")
    except (OSError, ValueError) as e:
        _dbg(f"读取浏览器级调试端点失败: {e}")
        return None


def _shutdown_browser(debug_port, proc):
    """抓取结束后的浏览器收尾：先走 CDP Browser.close 优雅关闭（窗口正常退出、
    不留「未正常关闭」恢复提示），失败再强制杀进程树兜底。

    为什么不只靠 taskkill：Edge/Chrome 在「启动加速/后台模式」等场景下，真实
    浏览器进程树可能脱离我们 spawn 的根进程（启动后立即交接退出），taskkill /T
    杀不到真正的窗口进程；CDP Browser.close 走调试协议，无论进程树归属如何
    都能关掉窗口。"""
    ws_url = _browser_ws(debug_port)
    if ws_url:
        try:
            CDP(ws_url).send("Browser.close", timeout=5)
        except (RuntimeError, TimeoutError, OSError) as e:
            _dbg(f"Browser.close 失败: {e}")
    # 等待进程退出（Browser.close 后 Chromium 通常 1 秒内退出）
    for _ in range(6):
        if proc is None or proc.poll() is not None:
            return
        time.sleep(0.5)
    _kill(proc)  # 兜底：进程树未退出则强制终止


def _kill(proc):
    try:
        if proc is None or proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, check=False)
        else:
            proc.terminate()
    except (OSError, subprocess.SubprocessError) as e:
        _dbg(f"关闭浏览器进程失败: {e}")


if __name__ == "__main__":
    sys.exit(main())

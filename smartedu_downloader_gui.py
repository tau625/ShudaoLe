#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
书到了（ShudaoLe / Book Arrived）· 教材下载 - 网页界面
===================================================

运行后自动打开浏览器（仅监听本机 127.0.0.1，不对外网暴露）:
    python smartedu_downloader_gui.py                 # 默认端口 8765
    python smartedu_downloader_gui.py --port 9000     # 指定端口
    python smartedu_downloader_gui.py --no-browser    # 不自动打开浏览器

说明:
  - 界面文件 smartedu_webui.html 需与本脚本放在同一目录
  - 复用 smartedu_downloader.py 的核心下载逻辑（解析/重试/令牌降级）
  - 纯 Python 标准库实现 HTTP 服务，无需安装额外依赖（requests 除外）
"""

import argparse
import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# 打包成 exe 后，静态资源（界面 html）被 PyInstaller 收集到运行时目录
# （onedir 下为 _internal，onefile 下为临时解压目录 sys._MEIPASS）；exe 自身在
# BASE_DIR。下面据此定位两类文件。
FROZEN = getattr(sys, "frozen", False)
_RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))

from smartedu_downloader import (  # noqa: E402
    DIM_LABELS, FILTER_DIMS, ENABLED_DIMS, AuthContext, CancelledError,
    catalog_facets, fetch_catalog_index, initial_token, process_one,
    search_catalog, publisher_facets, relax_suggestions,
    PUB_GROUPS, SUPPORTED_SCOPE, DEFAULT_FILTERS,
)
import auto_fetch_token  # noqa: E402  一键令牌抓取（进程内调用，随 exe 打包零依赖）

HTML_FILE = _RESOURCE_DIR / "smartedu_webui.html"
DEFAULT_PORT = 8765


def app_version() -> str:
    """界面显示用的版本号，直接解析 version_info.txt。

    与 exe 文件属性里的版本同源（打包时该文件随 exe 进 _internal，见 build.spec
    的 datas），因此发版只改 version_info.txt 一处，界面与文件属性自动同步——
    不会再出现「属性已是 1.0.1、界面还写着 1.0.0」这类漏改。

    解析失败返回 "unknown"：版本号只用于展示，不该因此让服务起不来。
    """
    try:
        text = (_RESOURCE_DIR / "version_info.txt").read_text(encoding="utf-8")
        m = re.search(r"StringStruct\(u'FileVersion',\s*u'([^']+)'\)", text)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "unknown"


APP_VERSION = app_version()

# ========== 一键获取令牌（进程内线程调用 auto_fetch_token.run_token_fetch） ==========
# 令牌抓取逻辑整合进 GUI 进程内运行（import auto_fetch_token 模块），不再 subprocess
# 调外部 Python，因此打包成 exe 后对方机器无需安装 Python，真正零依赖。
AUTO_TOKEN_STATE = {          # 仅由后台线程写入、HTTP 线程读取
    "running": False,
    "phase": "idle",          # idle|starting|waiting|done|error
    "message": "",
    "token": "",
    "error": "",
    "cancel": False,          # 用户主动放弃标志（线程内轮询检查）
}
AUTO_TOKEN_LOCK = threading.Lock()


def _browser_available():
    """快速探测是否有可用的浏览器（Edge/Chrome）"""
    return auto_fetch_token.find_browser() is not None


def auto_token_start():
    """在 GUI 进程内启动一键获取令牌（后台线程），立即返回（不阻塞 HTTP）"""
    with AUTO_TOKEN_LOCK:
        if AUTO_TOKEN_STATE["running"]:
            return False, "已在运行"
        AUTO_TOKEN_STATE.update(running=True, phase="starting",
                                message="正在准备浏览器...", token="", error="",
                                cancel=False)
        threading.Thread(target=_auto_token_worker, daemon=True).start()
        return True, ""


def _auto_token_worker():
    """后台线程：调用 auto_fetch_token.run_token_fetch 抓取令牌，实时回写状态。

    取消标志 AUTO_TOKEN_STATE["cancel"] 由 auto_token_abort() 置位，
    这里通过 cancel_check 回调传给抓取流程，实现"随时中止、及时清理浏览器"。
    """
    def emit(obj):
        with AUTO_TOKEN_LOCK:
            if obj.get("waiting"):
                AUTO_TOKEN_STATE.update(phase="waiting",
                                        message=obj.get("message", "等待登录..."))
            elif obj.get("ok"):
                AUTO_TOKEN_STATE.update(phase="done", running=False,
                                        token=obj.get("token", ""),
                                        message=obj.get("source", "已获取"),
                                        cancel=False)
            elif obj.get("ok") is False:
                AUTO_TOKEN_STATE.update(phase="error", running=False,
                                        error=obj.get("error", "未知错误"),
                                        message="", cancel=False)

    def cancel_check():
        with AUTO_TOKEN_LOCK:
            return AUTO_TOKEN_STATE["cancel"]

    try:
        auto_fetch_token.run_token_fetch(
            trigger_content=_pick_trigger_cid(),
            token_file=str(BASE_DIR / "token.txt"),
            timeout=180,
            login_url=os.environ.get("SMARTEDU_AUTO_URL", "").strip(),
            emit=emit,
            cancel_check=cancel_check,
        )
    except Exception as e:  # 兜底：抓取流程任何未预料异常都不至于让线程静默崩溃
        with AUTO_TOKEN_LOCK:
            AUTO_TOKEN_STATE.update(running=False, phase="error",
                                    error=f"自动获取异常: {e}", message="")
    finally:
        with AUTO_TOKEN_LOCK:
            # 无论成功/失败/取消，确保 running 归位（若 emit 未覆盖到）
            AUTO_TOKEN_STATE["running"] = False
            AUTO_TOKEN_STATE["cancel"] = False


def _pick_trigger_cid():
    """从目录缓存里挑一个真实教材 contentId 作为令牌触发页；无缓存则回退固定值。

    选一本人教版小学教材，保证详情页能加载在线阅读器并触发 private 资源请求。
    若 GUI 内存已加载目录则优先用内存数据。
    """
    try:
        items = None
        with _CATALOG_LOCK:
            if _CATALOG["items"] is not None:
                items = _CATALOG["items"]
        if not items:
            try:
                items = fetch_catalog_index(on_log=lambda m: None)
                with _CATALOG_LOCK:
                    _CATALOG["items"] = items
            except Exception:
                items = None
        if items:
            # 优先人教版小学；否则任意一本
            for it in items:
                pub = str(it.get("publisher_raw") or it.get("publisher") or "")
                ph = str(it.get("phase_raw") or it.get("phase") or "")
                if "人教" in pub and "小学" in ph and it.get("id"):
                    return it["id"]
            for it in items:
                if it.get("id"):
                    return it["id"]
    except Exception:
        pass
    # 内置兜底（探测验证可自动触发的教材）
    return "bdc00134-465d-454b-a541-dcd0cec4d86e"


def auto_token_status():
    """返回当前一键获取令牌的状态快照"""
    with AUTO_TOKEN_LOCK:
        s = AUTO_TOKEN_STATE.copy()
    s.pop("cancel", None)
    return s


def auto_token_abort():
    """用户主动放弃：置位取消标志，抓取线程会在下一轮检查时清理浏览器并退出"""
    with AUTO_TOKEN_LOCK:
        if not AUTO_TOKEN_STATE["running"]:
            return False, "当前没有正在进行的自动获取"
        AUTO_TOKEN_STATE["cancel"] = True
        AUTO_TOKEN_STATE.update(running=False, phase="idle",
                                message="已放弃", error="")
    return True, ""


# ---------- 下载目录选择 & 打开文件/目录 ----------
def _extract_path(msg):
    """从'文件已存在，跳过: <路径>'这类文案中提取存在的路径"""
    if not msg:
        return ""
    idx = msg.find(".pdf")
    if idx != -1:
        cand = msg[max(0, msg.rfind(":", 0, idx) + 1): idx + 4].strip()
        return cand if os.path.exists(cand) else ""
    return ""


def _is_within(path, base):
    """路径包含校验：path 规范化后必须位于 base 目录内（含相等）"""
    try:
        return Path(path).resolve().is_relative_to(Path(base).resolve())
    except (OSError, ValueError):
        return False


def pick_folder(start=""):
    """弹出系统资源管理器文件夹选择对话框。

    返回 (path, status)：
      - (path, "ok")        用户选中了文件夹，path 为绝对路径
      - ("", "cancelled")   用户点了取消 / 关闭对话框
      - ("", "error")       对话框或管道出错（如超时、非 Windows）
    注：仅 Windows 可用；其它平台返回 ("", "error")。
    """
    if os.name != "nt":
        return "", "error"
    # 关键：PowerShell 子进程默认用系统代码页(如 GBK)写 stdout，中文路径会被
    # 读成乱码 -> os.path.isdir 判 False -> 误判为"取消"。必须强制 UTF-8。
    ps = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "[Console]::InputEncoding=[System.Text.Encoding]::UTF8;"
        "[void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms');"
        "[void][System.Reflection.Assembly]::LoadWithPartialName('System.Drawing');"
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$d.Description = '请选择教材下载保存目录';"
        "$d.ShowNewFolderButton = $true;"
        # start 来自界面回传，必须转义单引号（''），否则可拼进任意 PowerShell 代码
        + (f"$d.SelectedPath = '{start.replace(chr(39), chr(39) * 2)}';" if start else "")
        + "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
        + "{ [Console]::Out.Write($d.SelectedPath) }"
    )
    try:
        # 打开资源管理器对话框需要在用户会话内显示，这里用普通启动；CREATE_NO_WINDOW 会遮黑窗
        r = subprocess.run(["powershell", "-NoProfile", "-STA",
                            "-Command", ps],
                           capture_output=True, text=True,
                           timeout=300, encoding="utf-8", errors="replace")
        out = (r.stdout or "").strip()
        if r.returncode != 0 or not out:
            # 取消时 PowerShell 正常退出且无输出；非 0 才是真正异常
            if r.returncode == 0:
                return "", "cancelled"
            add_log(f"目录选择对话框进程异常退出(code={r.returncode})")
            return "", "error"
        p = out.splitlines()[-1].strip()
        if os.path.isdir(p):
            return p, "ok"
        add_log(f"目录选择返回了无效路径: {p}")
        return "", "error"
    except subprocess.TimeoutExpired:
        add_log("目录选择对话框超时")
        return "", "error"
    except OSError as e:
        add_log(f"目录选择对话框异常: {e}")
        return "", "error"


def open_with_default(path, mode="file"):
    """用系统方式打开文件/目录。mode:
      - "file":   用系统默认程序打开文件（目录则打开该目录）
      - "dir":    打开目录（path 为目录）；若 path 是文件则打开其所在目录
      - "reveal": 在资源管理器中打开并定位 path（文件则选中它）；path 是目录则直接打开目录
    返回(ok, msg)。"""
    if not path:
        return False, "路径为空"
    p = os.path.normpath(path)
    if not os.path.exists(p):
        return False, f"路径不存在: {p}"
    is_dir = os.path.isdir(p)
    try:
        if os.name == "nt":
            if mode == "reveal":
                # explorer /select 会在资源管理器里打开所在目录并选中该项
                subprocess.Popen(["explorer", "/select,", p])
            elif mode == "dir" and not is_dir:
                subprocess.Popen(["explorer", os.path.dirname(p)])
            else:
                os.startfile(p)  # 文件用默认程序打开；目录用资源管理器打开
        else:
            opener = ["xdg-open"] if is_dir else ["xdg-open", p]
            subprocess.Popen(opener, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return True, ""
    except Exception as e:
        return False, f"打开失败: {e}"



# 筛选范围的权威定义（SUPPORTED_SCOPE / PUB_GROUPS / ENABLED_DIMS）与版本候选的
# 分组折叠（publisher_facets）、空结果放宽建议（relax_suggestions）均已下沉到
# smartedu_downloader.py，CLI 与 GUI 共用同一份规则，避免两处维护导致不一致。

# ---------- 共享状态（工作线程写，HTTP 线程读） ----------
LOCK = threading.Lock()
STATE = {
    "running": False,
    "cancel": False,
    "items": [],           # [{index, entry, title, status, downloaded, total, msg}]
    "log": deque(maxlen=500),
    "summary": None,       # {"ok": n, "skip": n, "fail": n, "total": n}
    "started_at": None,
    "finished_at": None,
}


def add_log(msg):
    stamp = datetime.now().strftime("%H:%M:%S")
    with LOCK:
        STATE["log"].append(f"[{stamp}] {msg}")


def preview(text, limit=70):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit - 3] + "..."


def parse_entries(links_text):
    """解析多行输入: 每行一条，忽略空行与 # 注释，去重保序"""
    entries = []
    for raw in (links_text or "").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return list(dict.fromkeys(entries))


# ---------- 教材目录（内存缓存 + 核心模块磁盘缓存） ----------
_CATALOG = {"items": None}
_CATALOG_LOCK = threading.Lock()


def get_catalog(refresh=False):
    """获取教材目录索引（带内存缓存；refresh=True 强制重新下载）"""
    with _CATALOG_LOCK:
        if not refresh and _CATALOG["items"] is not None:
            return _CATALOG["items"]
        items = fetch_catalog_index(force=refresh, on_log=add_log)
        _CATALOG["items"] = items
        return items


# ---------- 下载工作线程 ----------
def run_task(payload):
    try:
        entries = parse_entries(payload.get("links_text", ""))
        out_dir = Path(payload.get("output_dir") or "downloads").expanduser()
        retries = max(1, int(payload.get("retries") or 3))
        timeout = max(5, int(payload.get("timeout") or 30))
        token = (payload.get("token") or "").strip() or None
        save_token = bool(payload.get("save_token", True))
        flat_name = bool(payload.get("flat_name", False))
        if not entries:
            raise ValueError("未提供任何链接")
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        add_log(f"[配置错误] {e}")
        with LOCK:
            STATE["running"] = False
            STATE["summary"] = {"ok": 0, "skip": 0, "fail": 0, "total": 0}
        return

    # 令牌来源: 页面填写 > 环境变量 SMARTEDU_TOKEN > token.txt；界面模式不允许控制台交互输入
    auth = AuthContext(initial_token(token), save_token=save_token, interactive=False)

    items = [
        {"index": i, "entry": e, "title": None, "status": "pending",
         "downloaded": 0, "total": 0, "msg": "", "file": ""}
        for i, e in enumerate(entries, 1)
    ]
    with LOCK:
        STATE["items"] = items
        STATE["out_dir"] = str(out_dir.resolve())
    add_log(f"任务开始: 共 {len(entries)} 条 | 输出目录: {out_dir.resolve()}")
    if auth.token:
        add_log("已加载登录令牌（页面填写 / 环境变量 / token.txt）")

    def make_progress(item):
        def _cb(done, total):
            if STATE["cancel"]:
                raise CancelledError("用户取消")
            with LOCK:
                item["status"] = "downloading"
                item["downloaded"] = done
                item["total"] = total
        return _cb

    for item in items:
        if STATE["cancel"]:
            with LOCK:
                item["status"] = "fail"
                item["msg"] = "已取消"
            continue

        add_log(f"[{item['index']}/{len(items)}] {preview(item['entry'])}")
        with LOCK:
            item["status"] = "parsing"
        try:
            res = process_one(item["entry"], out_dir, auth, retries, timeout,
                              on_log=add_log, on_progress=make_progress(item),
                              item=item, flat_name=flat_name)
            with LOCK:
                item["status"] = res["status"]
                item["msg"] = res["msg"]
                if res.get("title"):
                    item["title"] = res["title"]
                # 记录下载文件路径：ok 时 msg 即完整路径；skip 时从提示中提取路径
                if res["status"] == "ok":
                    item["file"] = res.get("msg") or ""
                elif res["status"] == "skip":
                    item["file"] = _extract_path(res.get("msg") or "")
        except CancelledError:
            with LOCK:
                item["status"] = "fail"
                item["msg"] = "已取消"
            add_log("已取消当前下载")
        except Exception as e:  # 兜底：未预料异常不中断批量任务
            with LOCK:
                item["status"] = "fail"
                item["msg"] = f"未预料异常: {type(e).__name__}: {e}"
            add_log(f"未预料异常: {type(e).__name__}: {e}")

    ok = sum(1 for x in items if x["status"] == "ok")
    skip = sum(1 for x in items if x["status"] == "skip")
    fail = sum(1 for x in items if x["status"] == "fail")
    canceled = STATE["cancel"]
    with LOCK:
        STATE["running"] = False
        STATE["cancel"] = False  # 重置取消标志，避免界面按钮卡在"正在停止..."
        STATE["finished_at"] = datetime.now().strftime("%H:%M:%S")
        STATE["summary"] = {"ok": ok, "skip": skip, "fail": fail, "total": len(items)}
    add_log(f"任务结束: 成功 {ok} | 跳过 {skip} | 失败 {fail}（共 {len(items)}）"
            + ("（用户停止）" if canceled else ""))


# ---------- HTTP 服务 ----------
class Handler(BaseHTTPRequestHandler):
    server_version = "ShudaoLe/1.0"

    def _local_origin(self):
        """本服务只接受明确来自本机的请求。

        Host 校验：DNS 重绑定攻击会把攻击者域名解析到 127.0.0.1，此时浏览器
        发出的 Host 是攻击者域名而非本机地址，据此拦截，防止令牌/任务数据
        被第三方网页读取。
        Origin 校验（仅 POST）：浏览器发起的跨站 POST 一定带 Origin 头；
        同源页面与 curl 等本机工具通常不带。出现非本机 Origin 即判定为
        跨站伪造请求（CSRF），拒绝执行改状态操作。
        """
        port = self.server.server_address[1]
        local_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in local_hosts:
            return False
        if self.command == "POST":
            origin = (self.headers.get("Origin") or "").strip().lower()
            if origin and origin.rstrip("/") not in {
                    f"http://127.0.0.1:{port}", f"http://localhost:{port}",
                    f"http://[::1]:{port}"}:
                return False
        return True

    def _send(self, body, content_type, code=200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8", code)

    def do_GET(self):
        if not self._local_origin():
            self._send_json({"error": "拒绝非本机来源的请求"}, 403)
            return
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            try:
                self._send(HTML_FILE.read_text(encoding="utf-8"),
                           "text/html; charset=utf-8")
            except OSError as e:
                self._send_json({"error": f"界面文件缺失: {e}"}, 500)
        elif path in ("/api/catalog", "/api/facets"):
            q = parse_qs(urlsplit(self.path).query or "")
            refresh = "refresh" in q
            ids_only = (q.get("ids_only") or ["0"])[0] in ("1", "true")
            filters = {k: (v[0] or "") for k, v in q.items()
                       if k in FILTER_DIMS and (v[0] or "").strip()}
            keyword = (q.get("keyword") or [""])[0].strip()
            include_resources = (q.get("include_resources") or ["0"])[0] in ("1", "true")
            try:
                limit = int((q.get("limit") or ["300"])[0])
            except ValueError:
                limit = 300
            try:
                items = get_catalog(refresh=refresh)
                # ids_only：只要 id 列表，供"全部加入下载列表"绕开界面展示条数上限，
                # 否则用户以为加了全部，实际只加了前 limit 条。
                if ids_only:
                    hits = search_catalog(items, keyword=keyword,
                                          include_resources=include_resources,
                                          **filters)
                    self._send_json({"ok": True, "total": len(hits),
                                     "ids": [it["id"] for it in hits]})
                    return
                # 一次调用同时拿到全量命中与级联候选（内部复用，不重复遍历）
                result = catalog_facets(items, filters, keyword=keyword,
                                        include_resources=include_resources)
                matches = result["items"]
                # 版本维度下发「分组 + 真实标签」两级候选：平台把人教系的语文/数学/
                # 科学标成了三个不同标签，只给分组则无法精确选，只给标签则下全套要选三次
                result["facets"]["publisher"] = publisher_facets(
                    result["facets"].get("publisher", []))
                payload = {
                    "ok": True,
                    "total": len(matches),          # 命中总数（含版本组约束）
                    "returned": min(len(matches), limit),
                    "items": matches[:limit],       # 结果行（已按认知顺序排序）
                    "facets": result["facets"],     # 级联用的各维度可选值+计数
                    "dim_labels": DIM_LABELS,
                    "enabled_dims": list(ENABLED_DIMS),  # 前端据此动态渲染下拉
                    "scope": SUPPORTED_SCOPE,       # 完整支持范围（其余为占位）
                    "default_filters": DEFAULT_FILTERS,  # 首屏替用户预选的条件
                }
                if not matches:
                    # 空结果只说"没有匹配"毫无帮助；告诉用户去掉哪条能找回结果
                    payload["relax_hints"] = relax_suggestions(
                        items, filters, keyword=keyword,
                        include_resources=include_resources)
                self._send_json(payload)
            except Exception as e:
                self._send_json({"error": f"教材目录获取失败: {e}"}, 502)
        elif path == "/api/auto-token/status":
            self._send_json(auto_token_status())
        elif path == "/api/status":
            with LOCK:
                payload = {
                    "running": STATE["running"],
                    "cancel": STATE["cancel"],
                    "items": [dict(x) for x in STATE["items"]],
                    "log": list(STATE["log"])[-300:],
                    "summary": STATE["summary"],
                    "out_dir": STATE.get("out_dir", ""),
                    "started_at": STATE["started_at"],
                    "finished_at": STATE["finished_at"],
                    "version": APP_VERSION,
                }
            self._send_json(payload)
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._local_origin():
            self._send_json({"error": "拒绝非本机来源的请求"}, 403)
            return
        path = urlsplit(self.path).path
        if path == "/api/start":
            with LOCK:
                running = STATE["running"]
            if running:
                self._send_json({"error": "任务正在运行中，请先等待完成或点击停止"}, 409)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 1_000_000:
                    raise ValueError("请求体过大")
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if not isinstance(payload, dict):
                    raise ValueError("无效的请求数据")
            except Exception as e:
                self._send_json({"error": f"请求解析失败: {e}"}, 400)
                return
            with LOCK:
                STATE.update(running=True, cancel=False, items=[],
                             summary=None,
                             started_at=datetime.now().strftime("%H:%M:%S"),
                             finished_at=None)
                STATE["log"].clear()
            threading.Thread(target=run_task, args=(payload,), daemon=True).start()
            self._send_json({"ok": True})
        elif path == "/api/auto-token":
            # 前置检查：浏览器是否可用（令牌抓取逻辑已整合进进程内，无外部脚本依赖）
            if not _browser_available():
                self._send_json(
                    {"error": "未检测到 Edge/Chrome 浏览器，无法自动登录抓取令牌。"
                              "请手动获取并粘贴令牌。"}, 400)
                return
            ok, err = auto_token_start()
            if not ok:
                self._send_json({"error": err}, 409 if "运行" in err else 500)
                return
            add_log("已启动\"一键获取令牌\"：已打开浏览器，请在弹出的窗口登录平台")
            self._send_json({"ok": True})
        elif path == "/api/auto-token/abort":
            ok, msg = auto_token_abort()
            add_log("已放弃自动获取令牌")
            self._send_json({"ok": True, "message": msg})
        elif path == "/api/choose-dir":
            # 用系统资源管理器选择保存目录（会弹窗，等待用户选择后返回）
            try:
                length = int(self.headers.get("Content-Length") or 0)
                start = ""
                if length:
                    payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    start = str(payload.get("start") or "").strip()
            except Exception:
                start = ""
            chosen, status = pick_folder(start)
            if status == "ok" and chosen:
                self._send_json({"ok": True, "path": chosen})
            elif status == "cancelled":
                self._send_json({"ok": False, "cancelled": True,
                                 "error": "已取消选择"}, 400)
            else:
                self._send_json({"ok": False, "cancelled": False,
                                 "error": "目录选择失败，请重试"}, 400)
        elif path == "/api/open":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                target = str(payload.get("path") or "").strip()
                mode = str(payload.get("mode") or "file").strip()
                if mode not in ("file", "dir", "reveal"):
                    mode = "file"
            except Exception:
                target = ""
                mode = "file"
            if not target:
                self._send_json({"error": "缺少路径"}, 400)
                return
            # 打开动作只允许作用于当前任务的下载目录内，
            # 防止被诱导的请求用 os.startfile 打开/执行任意路径
            with LOCK:
                out_dir = STATE.get("out_dir", "")
            if not out_dir or not _is_within(target, out_dir):
                self._send_json({"error": "只允许打开下载目录内的文件"}, 403)
                return
            ok, msg = open_with_default(target, mode)
            if not ok:
                self._send_json({"error": msg}, 400)
                return
            self._send_json({"ok": True})
        elif path == "/api/cancel":
            with LOCK:
                # 仅在任务运行中才置位，防止任务刚结束时到达的取消请求污染状态
                if STATE["running"]:
                    STATE["cancel"] = True
                    stopping = True
                else:
                    stopping = False
            if stopping:
                add_log("收到停止请求，将在当前下载点中断...")
            self._send_json({"ok": True, "stopping": stopping})
        else:
            self._send_json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        pass  # 静默访问日志，避免控制台刷屏


class LocalServer(ThreadingHTTPServer):
    """本地服务：禁用地址复用。

    ThreadingHTTPServer 默认 allow_reuse_address=1，会让多个实例静默绑定同一端口，
    请求被随机分发到不同进程，表现为"点了下载没反应/进度不动"。置 0 后端口独占，
    重复启动会抛 OSError，配合单实例探测即可彻底避免多开。
    """
    allow_reuse_address = 0
    daemon_threads = True


def _probe_existing(port, timeout=1.5):
    """探测是否已有本程序实例在 port 上运行。

    必须绕过系统代理直接连 127.0.0.1（本机若开着 Clash/V2Ray 等代理，默认会走代理，
    探测结果不可信）。只有拿到符合本程序特征的 /api/status 响应才算"已有实例"，
    避免把占用同端口的其它程序误判成自己。
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/status", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return isinstance(data, dict) and "running" in data
    except Exception:
        return False


def _port_in_use(port):
    """判断端口是否被占用（不区分占用者是谁）"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _notify(title, msg):
    """弹原生消息框提示（windowed 模式无控制台，print 用户看不见）。

    仅 Windows 生效，失败则静默降级到 print，不影响主流程。
    """
    if os.name == "nt":
        try:
            # MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND：置顶并激活，
            # 否则重复启动时弹出的提示可能藏在其它窗口后面，用户以为"没反应"
            ctypes.windll.user32.MessageBoxW(
                0, msg, title, 0x40 | 0x40000 | 0x10000)
            return
        except Exception:
            pass
    print(f"{title}: {msg}")


def main():
    parser = argparse.ArgumentParser(
        description="书到了（ShudaoLe）· 国家中小学智慧教育平台教材 PDF 下载 - 网页界面")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"服务端口（默认: {DEFAULT_PORT}，被占用时自动换空闲端口）")
    parser.add_argument("--no-browser", action="store_true",
                        help="启动后不自动打开浏览器")
    args = parser.parse_args()

    if not HTML_FILE.exists():
        _notify("书到了", f"缺少界面文件: {HTML_FILE}\n"
                        f"请确保 smartedu_webui.html 与本脚本在同一目录。")
        return 1

    # 单实例保护：已有实例在跑则直接复用它的窗口并退出。
    # 否则重复双击 exe 会起多个进程，请求被随机分发，界面就会"点了没反应"。
    if _probe_existing(args.port):
        url = f"http://127.0.0.1:{args.port}"
        if not args.no_browser:
            webbrowser.open(url)
        _notify("书到了", f"书到了已在运行中，已为你打开现有窗口：\n{url}")
        return 0

    try:
        server = LocalServer(("127.0.0.1", args.port), Handler)
    except OSError:
        # 端口被其它程序占用（非本程序实例）-> 自动换成系统分配的空闲端口
        server = LocalServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}"

    print("=" * 56)
    print("书到了（ShudaoLe）· 国家中小学智慧教育平台教材 PDF 下载")
    print(f"  地址: {url}")
    print("  仅本机可访问；使用完毕后关闭本窗口或按 Ctrl+C 退出")
    print("=" * 56)

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    # 后台预载教材目录（有磁盘缓存则秒回；无则下载约 40MB，打开页面时若未就绪会显示加载中）
    def _warm_catalog():
        try:
            get_catalog()
            add_log("教材目录已就绪（可按 学段/年级/科目/版本 筛选）")
        except Exception as e:
            add_log(f"教材目录预载失败（打开页面时会自动重试）: {e}")
    threading.Thread(target=_warm_catalog, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

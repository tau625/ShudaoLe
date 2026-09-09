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
  - 复用 shudaole 包的核心下载逻辑（解析/重试/令牌降级）
  - 纯 Python 标准库实现 HTTP 服务，无需安装额外依赖（requests 除外）
"""

import argparse
import ctypes
import json
import os
import re
import shutil
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

# 包内相对导入：核心逻辑已拆入 shudaole 包（P1-1），根目录脚本保留为 thin shim
from ..config import TOKEN_FILE, path_is_relative_to  # noqa: E402
from ..catalog import (  # noqa: E402
    DIM_LABELS, FILTER_DIMS, ENABLED_DIMS, catalog_facets, fetch_catalog_index,
    search_catalog, publisher_facets, relax_suggestions,
    SUPPORTED_SCOPE, DEFAULT_FILTERS,
)
from ..download import (AuthContext, initial_token, download_many)
from ..logutil import attach_callback  # noqa: E402
from .. import token as auto_fetch_token  # noqa: E402  一键令牌抓取（进程内调用，随 exe 打包零依赖）

BASE_DIR = Path(__file__).resolve().parent

# 打包成 exe 后，静态资源（界面 html、version_info.txt）被 PyInstaller 收集到
# 运行时目录（onedir 下为 _internal，即 sys._MEIPASS）；源码运行时位于仓库根目录
# （包目录的上两级）。下面据此定位。
FROZEN = getattr(sys, "frozen", False)
_RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
if not FROZEN and not (_RESOURCE_DIR / "smartedu_webui.html").exists():
    _RESOURCE_DIR = BASE_DIR.parent.parent

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
            # 落盘位置由 shudaole.config 决定（用户配置目录，见 TOKEN_FILE）；
            # 这里只把程序目录作为兜底目录传进去（配置目录不可写时用它）。
            token_file=str(TOKEN_FILE),
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
    """返回当前一键获取令牌的状态快照。

    令牌只随 phase=done 的首次查询交付，交付即从内存状态清除（P1-6）——
    否则令牌会无限期滞留在状态 dict 里，任何本地进程轮询该端点都能读到。"""
    with AUTO_TOKEN_LOCK:
        s = AUTO_TOKEN_STATE.copy()
        if s.get("phase") == "done" and s.get("token"):
            AUTO_TOKEN_STATE["token"] = ""
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
        return path_is_relative_to(path, base)
    except (OSError, ValueError):
        return False


def pick_folder(start=""):
    """弹出系统目录选择对话框。

    返回 (path, status)：
      - (path, "ok")        用户选中了文件夹，path 为绝对路径
      - ("", "cancelled")   用户点了取消 / 关闭对话框
      - ("", "error")       对话框或管道出错（如超时、未装 zenity）
    按平台分发：Windows 用进程内 COM IFileDialog（资源管理器同款现代对话框），
    macOS 用 osascript 调 Finder，Linux 优先 zenity。都不可用时
    返回 ("", "error")，界面仍可手动输入保存路径。
    """
    if os.name == "nt":
        return _pick_folder_windows(start)
    if sys.platform == "darwin":
        return _pick_folder_macos()
    return _pick_folder_linux()


def _pick_folder_windows(start=""):
    """Windows：进程内 COM IFileDialog。

    旧方案起 PowerShell 子进程弹 WinForms FolderBrowserDialog，有两个
    用户可感知的毛病：①子进程带控制台黑窗闪现；②子进程未声明 DPI
    感知，高缩放屏上对话框被系统拉伸发糊，且那是老式树状对话框。
    现改为进程内 COM 调用（无子进程、现代对话框、进程级 DPI 感知），
    COM 路径异常时回退旧 PowerShell 方案兜底。
    """
    try:
        return _pick_folder_com(start)
    except Exception as e:  # COM 初始化/调用任何环节失败都不影响功能可用
        add_log(f"目录选择 COM 对话框异常，回退 PowerShell 方案: {e}")
        return _pick_folder_windows_ps(start)


# 进程级 DPI 感知只需设置一次
_dpi_aware_done = False


def _ensure_windows_dpi_aware():
    """声明 Per-Monitor V2 DPI 感知：对话框按屏幕真实缩放渲染，不再发糊。

    必须在创建任何窗口前调用；进程内唯一的窗口就是本对话框与消息框，
    设置只影响清晰度，无副作用。逐级降级到旧 API，全部失败也不致命。
    """
    global _dpi_aware_done
    if _dpi_aware_done or os.name != "nt":
        return
    _dpi_aware_done = True
    try:
        u32 = ctypes.windll.user32
        if hasattr(u32, "SetProcessDpiAwarenessContext"):
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = (HANDLE)-4
            if u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
        except Exception:
            u32.SetProcessDPIAware()
    except Exception:
        pass


def _pick_folder_com(start=""):
    """ctypes 直接调 COM IFileDialog（不依赖 pywin32/comtypes）。"""
    from ctypes import POINTER, byref, c_long, c_ulong, c_void_p, c_wchar_p

    _ensure_windows_dpi_aware()
    ole32 = ctypes.oledll.ole32
    shell32 = ctypes.windll.shell32

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    def make_guid(s):
        g = GUID()
        ole32.CLSIDFromString("{%s}" % s, byref(g))
        return g

    CLSID_FileOpenDialog = make_guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")
    IID_IFileDialog = make_guid("42F85136-DB7E-439C-85F1-E4075D135FC8")
    IID_IShellItem = make_guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")

    def vfunc(obj, index, restype, *argtypes):
        """取 COM 接口 obj vtable 第 index 项，包装成可调用函数（this 为首参）。"""
        vtbl = ctypes.cast(obj, POINTER(c_void_p))[0]
        fp = ctypes.cast(vtbl, POINTER(c_void_p))[index]
        return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(fp)

    hr_cancelled = 0x800704C7  # HRESULT_FROM_WIN32(ERROR_CANCELLED)

    ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    dlg = c_void_p()
    ole32.CoCreateInstance(byref(CLSID_FileOpenDialog), None, 1,  # CLSCTX_INPROC_SERVER
                           byref(IID_IFileDialog), byref(dlg))
    try:
        get_options = vfunc(dlg, 10, c_long, POINTER(c_ulong))
        set_options = vfunc(dlg, 9, c_long, c_ulong)
        set_title = vfunc(dlg, 17, c_long, c_wchar_p)
        set_folder = vfunc(dlg, 12, c_long, c_void_p)
        show = vfunc(dlg, 3, c_long, c_void_p)
        get_result = vfunc(dlg, 20, c_long, POINTER(c_void_p))
        release = vfunc(dlg, 2, c_ulong)

        opts = c_ulong()
        get_options(dlg, byref(opts))
        set_options(dlg, opts.value | 0x20 | 0x40)  # FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM
        set_title(dlg, "请选择教材下载保存目录")
        if start:
            # 初始定位到上次选择的目录；路径失效则静默跳过，不影响弹出
            try:
                item0 = c_void_p()
                shell32.SHCreateItemFromParsingName(str(start), None,
                                                    byref(IID_IShellItem), byref(item0))
                if item0:
                    set_folder(dlg, item0)
                    vfunc(item0, 2, c_ulong)(item0)
            except Exception:
                pass

        hr = show(dlg, None)
        if hr == hr_cancelled or (hr & 0xFFFF) == 0x04C7:
            return "", "cancelled"
        if hr != 0:
            raise OSError(f"IFileDialog.Show hr=0x{hr & 0xFFFFFFFF:08X}")

        item = c_void_p()
        get_result(dlg, byref(item))
        try:
            pw = c_void_p()
            vfunc(item, 5, c_long, c_ulong, POINTER(c_void_p))(item, 0x80058000, byref(pw))
            try:
                path = ctypes.wstring_at(pw)
            finally:
                ole32.CoTaskMemFree(pw)
        finally:
            vfunc(item, 2, c_ulong)(item)
    finally:
        release(dlg)
        ole32.CoUninitialize()

    if path and os.path.isdir(path):
        return path, "ok"
    add_log(f"目录选择返回了无效路径: {path}")
    return "", "error"


def _pick_folder_windows_ps(start=""):
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


def _pick_folder_macos():
    """macOS：osascript 调 Finder 的 choose folder（用户取消返回 -128）"""
    script = 'POSIX path of (choose folder with prompt "请选择教材下载保存目录")'
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=300)
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out and os.path.isdir(out):
            return out, "ok"
        if "-128" in (r.stderr or ""):
            return "", "cancelled"
        add_log(f"目录选择对话框进程异常退出(code={r.returncode})")
        return "", "error"
    except (subprocess.TimeoutExpired, OSError) as e:
        add_log(f"目录选择对话框异常: {e}")
        return "", "error"


def _pick_folder_linux():
    """Linux：优先 zenity；未安装返回 error（界面仍可手动输入路径）"""
    if not shutil.which("zenity"):
        return "", "error"
    try:
        r = subprocess.run(
            ["zenity", "--file-selection", "--directory",
             "--title", "请选择教材下载保存目录"],
            capture_output=True, text=True, timeout=300)
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out and os.path.isdir(out):
            return out, "ok"
        if r.returncode == 1:
            return "", "cancelled"
        return "", "error"
    except (subprocess.TimeoutExpired, OSError) as e:
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
        elif sys.platform == "darwin":
            if mode == "reveal":
                subprocess.Popen(["open", "-R", p])  # Finder 中定位该项
            elif mode == "dir" and not is_dir:
                subprocess.Popen(["open", os.path.dirname(p)])
            else:
                subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return True, ""
    except Exception as e:
        return False, f"打开失败: {e}"



# 筛选范围的权威定义（SUPPORTED_SCOPE / PUB_GROUPS / ENABLED_DIMS）与版本候选的
# 分组折叠（publisher_facets）、空结果放宽建议（relax_suggestions）均已下沉到
# shudaole.catalog，CLI 与 GUI 共用同一份规则，避免两处维护导致不一致。

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

    # P2-10：让「记住令牌」勾选框真正生效——勾选且页面填写了令牌时落盘
    # token.txt（AuthContext.save_token 只作用于交互式粘贴路径，GUI 恒为
    # interactive=False 所以原先从不落盘，勾选框形同虚设）。
    if save_token and token:
        try:
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(token, encoding="utf-8")
            if os.name != "nt":  # POSIX：令牌只属主可读写
                try:
                    os.chmod(TOKEN_FILE, 0o600)
                except OSError:
                    pass
        except OSError:
            pass  # 落盘失败不影响本次下载（令牌仍在内存里）

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

    # P2-1：并发下载。worker 池在 download_many 内部，取消走 Event（文件间隙 +
    # 进度回调两处响应），进行中的文件保留 .part 供续传。并发度可由界面传入。
    import threading as _threading
    cancel_event = _threading.Event()
    STATE["_cancel_event"] = cancel_event
    workers = max(1, min(5, int(payload.get("workers") or 3)))

    def _on_start(idx):
        item = items[idx]
        add_log(f"[{item['index']}/{len(items)}] {preview(item['entry'])}")
        with LOCK:
            item["status"] = "parsing"

    def _on_done(idx, final_item):
        item = items[idx]
        with LOCK:
            item["status"] = final_item["status"]
            item["msg"] = final_item["msg"]
            if final_item.get("title"):
                item["title"] = final_item["title"]
            item["file"] = final_item.get("file") or ""

    def _on_progress(idx, done, total):
        item = items[idx]
        with LOCK:
            item["status"] = "downloading"
            item["downloaded"] = done
            item["total"] = total

    # 取消按钮 -> Event（进度回调内抛 CancelledError 让 worker 优雅收尾）
    def _watch_cancel():
        while STATE["running"]:
            if STATE["cancel"]:
                cancel_event.set()
                return
            cancel_event.wait(0.3)

    import threading as _th
    _th.Thread(target=_watch_cancel, daemon=True).start()

    # P2-2：会话持久化——周期落盘（后台守护线程），崩溃/关界面后可恢复
    from .. import tasks as tasks_store
    _stop_save = _th.Event()

    def _periodic_save():
        while not _stop_save.wait(2.0):
            with LOCK:
                snapshot = {
                    "entries": entries, "out_dir": str(out_dir),
                    "workers": workers, "flat_name": flat_name,
                    "items": [dict(x) for x in items],
                }
            tasks_store.save_session(snapshot)

    _th.Thread(target=_periodic_save, daemon=True).start()

    download_many(
        entries, out_dir, auth, retries=retries, timeout=timeout,
        workers=workers, flat_name=flat_name, on_log=None,
        on_item_start=_on_start, on_item_done=_on_done,
        on_progress=_on_progress, cancel_event=cancel_event,
    )
    _stop_save.set()

    ok = sum(1 for x in items if x["status"] == "ok")
    skip = sum(1 for x in items if x["status"] == "skip")
    fail = sum(1 for x in items if x["status"] == "fail")
    canceled = STATE["cancel"]
    # 全部到达终态即视为完成，清除持久化；有未完成条目（取消/崩溃）保留供恢复
    unfinished = [x for x in items if x["status"] not in ("ok", "skip", "fail")]
    if not unfinished:
        tasks_store.clear_session()
    else:
        with LOCK:
            tasks_store.save_session({
                "entries": entries, "out_dir": str(out_dir),
                "workers": workers, "flat_name": flat_name,
                "items": [dict(x) for x in items],
            })
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
        else:
            # GET 也挡跨站滥用（P1-7）：外部页面可以用 <img src=".../api/catalog?refresh=1">
            # 无声触发 40MB 目录重下（DoS 级骚扰）。跨站 GET 一定带外域 Referer；
            # 本机页面带本机 Referer；curl 等工具不带 Referer——只拦"带了但非本机"。
            referer = (self.headers.get("Referer") or "").strip().lower()
            if referer and urlsplit(referer).netloc not in {
                    f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}:
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

    # ---------- 路由处理方法（P3-2：从 do_GET/do_POST 的 if-elif 抽出） ----------
    # 说明：各方法体与重构前的分支体逐行一致，仅把「路径 -> 方法」的映射改为
    # 类级字典 GET_ROUTES / POST_ROUTES 分发。新增端点 = 加一个方法 + 一行表项。

    def _read_json_body(self, max_size=1_000_000):
        """读取并解析 POST 的 JSON 请求体（重复三处的公共逻辑收敛）。

        成功返回 dict；失败抛 ValueError（含可展示的错误消息）。
        空请求体按空对象处理。
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > max_size:
                raise ValueError("请求体过大")
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"请求解析失败: {e}")
        if not isinstance(payload, dict):
            raise ValueError("无效的请求数据")
        return payload

    # ----- GET -----

    def _route_index(self):
        try:
            self._send(HTML_FILE.read_text(encoding="utf-8"),
                       "text/html; charset=utf-8")
        except OSError as e:
            self._send_json({"error": f"界面文件缺失: {e}"}, 500)

    def _route_catalog(self):
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

    def _route_auto_token_status(self):
        self._send_json(auto_token_status())

    def _route_pending_session(self):
        # P2-2：启动时查询上次未完成的任务会话（继续/放弃）
        try:
            from .. import tasks as tasks_store
            sess = tasks_store.load_session()
            if sess and tasks_store.has_unfinished(sess):
                self._send_json({
                    "pending": True,
                    "out_dir": sess.get("out_dir", ""),
                    "entries": sess.get("entries", []),
                    "counts": {
                        "ok": sum(1 for x in sess.get("items") or []
                                  if x.get("status") == "ok"),
                        "total": len(sess.get("entries") or []),
                    },
                    "saved_at": sess.get("saved_at", 0),
                })
            else:
                self._send_json({"pending": False})
        except Exception:
            self._send_json({"pending": False})

    def _route_update_check(self):
        # P2-5：查询最新版本（无遥测；失败/无新版返回 latest:null）
        from .. import update as upd
        res = upd.check_latest(APP_VERSION, force=True)
        self._send_json({"latest": res} if res else {"latest": None})

    def _route_status(self):
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

    # ----- POST -----

    def _route_start(self):
        try:
            payload = self._read_json_body()
        except ValueError as e:
            self._send_json({"error": f"请求解析失败: {e}"}, 400)
            return
        # P1-5：检查与置位必须在同一锁段——原先分属两个锁段，快速双击
        # 「开始下载」时两个请求都在对方置位前通过检查，会双开任务
        with LOCK:
            if STATE["running"]:
                busy = True
            else:
                busy = False
                STATE.update(running=True, cancel=False, items=[],
                             summary=None,
                             started_at=datetime.now().strftime("%H:%M:%S"),
                             finished_at=None)
                STATE["log"].clear()
        if busy:
            self._send_json({"error": "任务正在运行中，请先等待完成或点击停止"}, 409)
            return
        threading.Thread(target=run_task, args=(payload,), daemon=True).start()
        self._send_json({"ok": True})

    def _route_auto_token(self):
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

    def _route_auto_token_abort(self):
        ok, msg = auto_token_abort()
        add_log("已放弃自动获取令牌")
        self._send_json({"ok": True, "message": msg})

    def _route_choose_dir(self):
        # 用系统资源管理器选择保存目录（会弹窗，等待用户选择后返回）
        try:
            payload = self._read_json_body()
            start = str(payload.get("start") or "").strip()
        except ValueError:
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

    def _route_open(self):
        try:
            payload = self._read_json_body()
            # path=本地路径；target=外部 URL（P2-5 更新横幅用，P0-1 此前后端
            # 只认 path 导致「查看新版/一键下载」按钮从未生效）
            target = str(payload.get("path") or payload.get("target") or "").strip()
            mode = str(payload.get("mode") or "file").strip()
            if mode not in ("file", "dir", "reveal", "url"):
                mode = "file"
        except ValueError:
            target = ""
            mode = "file"
        if not target:
            self._send_json({"error": "缺少路径"}, 400)
            return
        if mode == "url":
            # URL 模式只放行 GitHub 相关 https 链接（更新横幅是唯一调用方），
            # 防止被诱导的请求把任意 scheme/地址塞给浏览器打开
            parts = urlsplit(target)
            host = (parts.hostname or "").lower()
            if parts.scheme != "https" or not (
                    host == "github.com" or host.endswith(".github.com")):
                self._send_json({"error": "只允许打开 GitHub 链接"}, 403)
                return
            webbrowser.open(target)
            self._send_json({"ok": True})
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

    def _route_cancel(self):
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

    def _route_discard_session(self):
        # P2-2：放弃恢复上次会话（保留已下载文件，只清持久化记录）
        try:
            from .. import tasks as tasks_store
            tasks_store.clear_session()
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # 路由表：路径 -> 处理方法（P3-2）。/api/facets 是 /api/catalog 的历史别名。
    GET_ROUTES = {
        "/": _route_index,
        "/index.html": _route_index,
        "/api/catalog": _route_catalog,
        "/api/facets": _route_catalog,
        "/api/auto-token/status": _route_auto_token_status,
        "/api/pending-session": _route_pending_session,
        "/api/update-check": _route_update_check,
        "/api/status": _route_status,
    }
    POST_ROUTES = {
        "/api/start": _route_start,
        "/api/auto-token": _route_auto_token,
        "/api/auto-token/abort": _route_auto_token_abort,
        "/api/choose-dir": _route_choose_dir,
        "/api/open": _route_open,
        "/api/cancel": _route_cancel,
        "/api/discard-session": _route_discard_session,
    }

    def do_GET(self):
        if not self._local_origin():
            self._send_json({"error": "拒绝非本机来源的请求"}, 403)
            return
        path = urlsplit(self.path).path
        handler = self.GET_ROUTES.get(path)
        if handler is None:
            self._send_json({"error": "not found"}, 404)
            return
        handler(self)

    def do_POST(self):
        if not self._local_origin():
            self._send_json({"error": "拒绝非本机来源的请求"}, 403)
            return
        path = urlsplit(self.path).path
        handler = self.POST_ROUTES.get(path)
        if handler is None:
            self._send_json({"error": "not found"}, 404)
            return
        handler(self)

    def log_message(self, fmt, *args):
        pass  # 静默访问日志，避免控制台刷屏


class LocalServer(ThreadingHTTPServer):
    """本地服务：禁用地址复用。

    ThreadingHTTPServer 默认 allow_reuse_address=1，会让多个实例静默绑定同一端口，
    请求被随机分发到不同进程，表现为"点了下载没反应/进度不动"。置 0 后端口独占，
    重复启动会抛 OSError，配合单实例探测即可彻底避免多开。
    """
    allow_reuse_address = False
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


def _acquire_app_mutex():
    """Windows：创建命名互斥量供 Inno Setup 安装器检测「程序正在运行」。

    返回内核句柄（进程存活期间保持有效，退出时由系统释放）；
    非 Windows 或创建失败返回 None（安装器届时回退到文件占用检测）。
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, "ShudaoLeAppMutex")
        return handle or None
    except Exception:
        return None


def _find_browser_exe():
    """找一台 Chrome 系浏览器可执行文件（支持 --app 独立窗口模式）。

    按平台探测常见安装位置；找不到返回 None，调用方回退系统默认浏览器。
    """
    candidates = []
    if os.name == "nt":
        candidates += [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        # 文件位置探测失败再查注册表 App Paths（覆盖自定义安装路径）
        try:
            import winreg
            for exe, root in (("msedge.exe", winreg.HKEY_LOCAL_MACHINE),
                              ("msedge.exe", winreg.HKEY_CURRENT_USER),
                              ("chrome.exe", winreg.HKEY_LOCAL_MACHINE),
                              ("chrome.exe", winreg.HKEY_CURRENT_USER)):
                try:
                    with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows"
                                             rf"\CurrentVersion\App Paths\{exe}") as k:
                        candidates.append(winreg.QueryValueEx(k, None)[0])
                except OSError:
                    continue
        except Exception:
            pass
    elif sys.platform == "darwin":
        candidates += [
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    else:
        for name in ("microsoft-edge", "microsoft-edge-stable", "google-chrome-stable",
                     "google-chrome", "chromium-browser", "chromium", "brave-browser"):
            w = shutil.which(name)
            if w:
                candidates.append(w)
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


def _open_in_app_mode(url):
    """用 Chrome 系浏览器的 --app 模式打开界面：无地址栏、任务栏独立图标、
    自带窗口标题，观感即桌面应用。找不到合适浏览器时回退系统默认浏览器。
    """
    exe = _find_browser_exe()
    if exe:
        try:
            subprocess.Popen([exe, "--app=" + url, "--window-size=1120,820"],
                             close_fds=(os.name != "nt"))
            add_log(f"已以独立窗口模式打开界面: {os.path.basename(exe)}")
            return
        except OSError as e:
            add_log(f"独立窗口模式启动失败({e})，回退默认浏览器")
    webbrowser.open(url)


def main():
    _app_mutex = _acquire_app_mutex()  # noqa: F841 保活到进程退出
    attach_callback(add_log)  # P1-3：shudaole.* 日志同源进界面缓冲
    from .. import pubs as _pubs; from .. import catalog as _catalog_mod
    _pubs.apply_user_pubs(_catalog_mod)  # P2-3：用户级出版社配置合并
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
            _open_in_app_mode(url)
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
        threading.Timer(0.6, lambda: _open_in_app_mode(url)).start()

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

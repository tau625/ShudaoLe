# -*- coding: utf-8 -*-
"""下载内核：详情获取、候选地址提取、重试下载、令牌管理与单条流程。"""

import os
import sys
import time

import requests

from .errors import (NeedAuthError, BadContentError, IncompleteDownload,
                     TransientError, DownloadError, DetailFetchError,
                     CancelledError)
from .net import build_session, validate_public_http_url
from .config import TOKEN_FILE, TOKEN_FILE_LEGACY, TOKEN_HELP, path_is_relative_to
from .logutil import get_logger

_log = get_logger()


def _default_log(msg):
    """无 on_log 回调时的日志出口：统一走 shudaole logger（P1-3）。"""
    _log.info(msg)
from .naming import (parse_content_id, extract_metadata, build_filename,
                     resolve_dest, CONTENT_TYPE_RE)

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False  # 缺少 tqdm 时使用内置百分比进度显示

DETAIL_ENDPOINTS = [
    "https://s-file-1.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/{cid}.json",
    "https://s-file-2.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/{cid}.json",
    "https://s-file-3.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/{cid}.json",
    "https://s-file-1.ykt.cbern.com.cn/zxx/ndrs/tch_material/details/{cid}.json",   # 旧版接口兜底
    "https://s-file-2.ykt.cbern.com.cn/zxx/ndrs/tch_material/details/{cid}.json",
]

# ti_storages 为空时按固定模式构造 PDF 候选地址（r1/r2/r3 互为镜像）

PDF_URL_TEMPLATE = "https://r{n}-ndr.ykt.cbern.com.cn/edu_product/esp/assets_document/{cid}.pkg/pdf.pdf"


def fetch_detail(cid, session, timeout):
    """轮询多个 CDN 端点获取教材详情 JSON"""
    last_err = None
    all_errs = []
    for tmpl in DETAIL_ENDPOINTS:
        url = tmpl.format(cid=cid)
        try:
            resp = session.get(validate_public_http_url(url), timeout=timeout)
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code} @ {url.split('/')[2]}"
                all_errs.append(last_err)
                continue
            try:
                data = resp.json()
            except ValueError:
                last_err = f"响应非 JSON @ {url.split('/')[2]}"
                all_errs.append(last_err)
                continue
            if isinstance(data, dict) and (data.get("ti_items") or data.get("title")):
                return data
            last_err = f"JSON 缺少关键字段 @ {url.split('/')[2]}"
            all_errs.append(last_err)
        except requests.RequestException as e:
            last_err = f"{type(e).__name__} @ {url.split('/')[2]}"
            all_errs.append(last_err)
    if all_errs and all("HTTP 403" in e for e in all_errs):
        raise DetailFetchError("HTTP 403，该资源可能已被平台下架或限制访问")
    raise DetailFetchError(last_err or "全部端点均失败")



def extract_candidates(detail, cid):
    """从详情 JSON 中提取 (教材标题, PDF 候选地址列表)"""
    title = (detail.get("title") or "").strip()
    if not title:
        # global_title 兜底：结构一般为 {"zh-CN": [...]} 或 {"zh-CN": "..."}
        gt = detail.get("global_title") or {}
        zh = gt.get("zh-CN") if isinstance(gt, dict) else None
        if isinstance(zh, list):
            title = " ".join(str(x) for x in zh).strip()
        elif isinstance(zh, str):
            title = zh.strip()

    urls_pdf_flag = []    # ti_file_flag 含 pdf 的条目地址（source pdf，最可靠）
    urls_pdf_suffix = []  # 其余 .pdf 结尾地址
    for item in detail.get("ti_items") or []:
        flag = str(item.get("ti_file_flag") or "").lower()
        for u in item.get("ti_storages") or []:
            if not (isinstance(u, str) and u.strip()):
                continue
            u = u.strip()
            if "pdf" in flag and u.lower().endswith(".pdf"):
                urls_pdf_flag.append(u)
            elif u.lower().endswith(".pdf"):
                urls_pdf_suffix.append(u)

    ordered = []
    for u in urls_pdf_flag + urls_pdf_suffix:
        ordered.append(u)
        # 同路径的非 private 主机变体（历史可用技巧，作为备用线路）
        if "-private" in u:
            alt = u.replace("-private", "")
            if alt not in ordered:
                ordered.append(alt)
    # 兜底: 旧版固定模式地址（部分老教材仍走该路径）
    for n in (1, 2, 3):
        alt = PDF_URL_TEMPLATE.format(n=n, cid=cid)
        if alt not in ordered:
            ordered.append(alt)
    return title, ordered



def append_access_token(url, token):
    """URL 查询参数方式携带令牌（部分线路支持，作为备选方案）"""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}accessToken={token}"



# ---------- 进度显示 ----------
class ProgressPrinter:
    """优先使用 tqdm；未安装时用 \\r 单行百分比刷新"""

    def __init__(self, label, total):
        self.label = (label or "下载")[:20]
        self.total = total
        self.downloaded = 0
        self._last_len = 0
        self._bar = None
        if HAS_TQDM:
            self._bar = tqdm(total=total if total > 0 else None,
                             unit="B", unit_scale=True, unit_divisor=1024,
                             desc=self.label, leave=False)

    def update(self, n):
        self.downloaded += n
        if self._bar is not None:
            self._bar.update(n)
        else:
            self._manual()

    def _manual(self):
        mb = self.downloaded / 1048576
        if self.total > 0:
            pct = min(100.0, self.downloaded * 100.0 / self.total)
            line = f"    {self.label}: {pct:5.1f}%  {mb:.1f}/{self.total / 1048576:.1f} MB"
        else:
            line = f"    {self.label}: 已下载 {mb:.1f} MB"
        line = line.ljust(self._last_len)   # 覆盖上一行残留字符
        self._last_len = len(line)
        sys.stdout.write("\r" + line)
        sys.stdout.flush()

    def close(self):
        if self._bar is not None:
            self._bar.close()
        else:
            sys.stdout.write("\n")
            sys.stdout.flush()



# ---------- 下载核心 ----------
def _cleanup(tmp):
    try:
        if tmp.exists():
            tmp.unlink()
    except OSError:
        pass



PDF_MAGIC = b"%PDF"

PDF_EOF = b"%%EOF"



def verify_pdf(path, expected_size=None):
    """校验已下载文件是否为完整可用的 PDF，返回 (是否通过, 原因)。

    三重校验缺一不可：长度一致（防截断）、%PDF 魔数头（防错误页伪装）、
    %%EOF 结束标记（防尾部截断——PDF 阅读器对缺尾文件会报"文件已损坏"）。"""
    try:
        size = path.stat().st_size
    except OSError:
        return False, "临时文件已丢失"
    if size < 1024:
        return False, f"文件过小（{size} 字节），疑似错误页"
    if expected_size and size != expected_size:
        return False, f"文件大小不符（{size}/{expected_size} 字节）"
    with open(path, "rb") as f:
        if not f.read(5).startswith(PDF_MAGIC):
            return False, "文件头不是 %PDF，服务器返回的不是 PDF"
        f.seek(max(0, size - 8192))
        if PDF_EOF not in f.read():
            return False, "缺少 %%EOF 结束标记，文件已截断"
    return True, ""



def download_file(urls, dest, session, retries=3, timeout=30, label="", on_progress=None):
    """按候选地址列表依次尝试下载（每条地址重试 retries 次，退避 1s/2s/4s）。

    可靠性设计：
      - 断点续传：已存在的 .part 用 Range 请求续传，网络中断不必从头再来
      - 三重校验：长度 + %PDF 头 + %%EOF 尾，任一项不通过即判为损坏并重下
      - 主机轮换：urls 为 s-file-1/2/3 等多个镜像，单条失败自动换下一个
    成功返回 True；需要令牌抛 NeedAuthError；其余失败抛 DownloadError。
    on_progress: 进度回调 on_progress(已下载字节, 总字节)；传入后不再使用控制台进度显示。"""
    last_err = None
    for url in urls:
        for attempt in range(1, max(1, retries) + 1):
            tmp = dest.with_name(dest.name + ".part")
            # dest 已由 resolve_dest 做过目录包含校验；对落盘的 .part 再显式校验一次
            if not path_is_relative_to(tmp, dest.resolve().parent):
                raise DownloadError(f"临时文件路径越界，拒绝写入: {tmp}")
            resp = None
            try:
                resume = tmp.stat().st_size if tmp.exists() else 0
                headers = {"Range": f"bytes={resume}-"} if resume else {}
                resp = session.get(validate_public_http_url(url), stream=True, timeout=timeout, headers=headers)
                if resp.status_code in (401, 403):
                    raise NeedAuthError(f"HTTP {resp.status_code}")
                if resp.status_code == 416:      # Range 越界：残留文件异常，重来
                    _cleanup(tmp)
                    raise IncompleteDownload("续传位置越界，重新下载")
                if resp.status_code >= 500 or resp.status_code == 429:
                    # 服务端临时故障，重试有望恢复；4xx（除 401/403）则直接判定失败
                    raise TransientError(f"HTTP {resp.status_code}（服务端临时故障）")
                if resp.status_code not in (200, 206):
                    raise DownloadError(f"HTTP {resp.status_code}")

                continuing = (resp.status_code == 206)
                if continuing:                  # Content-Range: bytes a-b/total
                    _, _, rng = (resp.headers.get("Content-Range") or "").partition("/")
                    total = int(rng or 0) or None
                else:
                    total = int(resp.headers.get("Content-Length") or 0) or None
                    if resume:                  # 服务端忽略 Range -> 从头写
                        resume = 0
                sent = resume
                progress = None if on_progress is not None else ProgressPrinter(label, total)
                if on_progress is not None:
                    on_progress(sent, total)
                need_magic = not continuing     # 续传时文件头已在 .part 里
                with tmp.open("ab" if continuing else "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        if need_magic:
                            head = chunk[:1024]
                            if not head.lstrip().startswith(PDF_MAGIC) or b"<html" in head.lower():
                                raise BadContentError("服务器返回的不是 PDF 内容")
                            need_magic = False
                        f.write(chunk)
                        sent += len(chunk)
                        if on_progress is not None:
                            on_progress(sent, total)
                        else:
                            progress.update(len(chunk))
                if progress is not None:
                    progress.close()
                elif on_progress is not None:
                    on_progress(sent, total)     # 保证结束时进度回调收尾

                if total and sent != total:
                    raise IncompleteDownload(f"下载不完整（{sent}/{total} 字节）")
                ok, why = verify_pdf(tmp, total)
                if not ok:
                    raise BadContentError(why)
                tmp.replace(dest)
                return True
            except NeedAuthError:
                _cleanup(tmp)
                raise
            except (KeyboardInterrupt, CancelledError):
                raise                            # 保留 .part，下次可续传
            except (IncompleteDownload, TransientError) as e:
                last_err = e                     # 保留 .part，下轮续传/重试
            except BadContentError as e:
                _cleanup(tmp)                    # 内容损坏，不能续传，整份重来
                last_err = e
            except (requests.RequestException, OSError) as e:
                last_err = e                     # 网络/磁盘错误，保留 .part 续传
            finally:
                if resp is not None:
                    resp.close()
            if attempt < max(1, retries):
                time.sleep(min(2 ** (attempt - 1), 4))  # 退避 1s/2s/4s
    raise DownloadError(str(last_err) if last_err else "未知错误")



# ---------- 令牌管理 ----------
def initial_token(cli_token):
    """令牌来源优先级: --token 参数 > 环境变量 SMARTEDU_TOKEN > 令牌文件

    令牌文件按「用户配置目录 ~/.config/shudaole/token.txt > 程序同目录 token.txt」
    顺序查找，两个位置都读——老版本用户升级后旧令牌依然生效。
    """
    if cli_token and cli_token.strip():
        return cli_token.strip()
    env = (os.environ.get("SMARTEDU_TOKEN") or "").strip()
    if env:
        return env
    for path in (TOKEN_FILE, TOKEN_FILE_LEGACY):
        if path.exists():
            try:
                t = path.read_text(encoding="utf-8", errors="replace").strip()
                if t:
                    return t
            except OSError:
                continue
    return None



class AuthContext:
    """保存令牌与会话；401 时负责获取新令牌并重建会话"""

    def __init__(self, token=None, save_token=True, interactive=True):
        self.save_token = save_token
        self.interactive = interactive  # 是否允许在控制台交互式粘贴令牌（界面模式应为 False）
        self.token = (token or "").strip() or None
        self.session = build_session(self.token)
        self.declined = False  # 用户明确跳过令牌输入后，本次运行不再重复提示

    def apply_token(self, token):
        self.token = token
        self.session = build_session(token)

    def obtain_token(self, on_log=None):
        """401 后尝试获取令牌；仅交互环境支持粘贴输入。成功则保存并返回。"""
        log = on_log or _default_log
        if self.declined:
            return None
        log(TOKEN_HELP)
        if not self.interactive or not sys.stdin or not sys.stdin.isatty():
            self.declined = True
            log("  当前为非交互环境，无法输入令牌。"
                "可改用 --token 参数 / SMARTEDU_TOKEN 环境变量 / token.txt 文件提供。")
            return None
        try:
            token = input("  请粘贴 x-nd-auth 的值后回车（直接回车跳过）: ").strip()
        except (EOFError, KeyboardInterrupt):
            self.declined = True
            return None
        if not token:
            self.declined = True
            return None
        if self.save_token:
            try:
                TOKEN_FILE.write_text(token, encoding="utf-8")
                if os.name != "nt":  # POSIX：令牌只属主可读写
                    try:
                        os.chmod(TOKEN_FILE, 0o600)
                    except OSError:
                        pass
                log(f"  已将令牌保存到 {TOKEN_FILE}（下次运行自动使用）")
            except OSError as e:
                log(f"  令牌保存失败: {e}")
        return token



# ---------- 单条处理 ----------
def _result(entry, title, status, msg):
    return {"entry": entry, "title": title, "status": status, "msg": msg}



def process_one(entry, out_dir, auth, retries, timeout,
                on_log=None, on_progress=None, item=None, flat_name=False):
    """处理一条输入：解析 -> 取详情 -> 提取地址 -> 下载（含 401 令牌降级）。

    on_log:      日志回调（None 时打印到控制台）
    on_progress: 下载进度回调 on_progress(已下载字节, 总字节)；None 时用控制台进度条
    item:        可选字典（供界面实时展示），其中 标题/状态 会被就地更新
    flat_name:   True 时用纯书名命名（历史行为）；False 时用结构化命名
    """
    def _log(msg):
        (on_log or _default_log)(msg)

    def _set(status):
        if item is not None:
            item["status"] = status

    # 仅支持教材详情页（contentType=assets_document）
    ct = CONTENT_TYPE_RE.search(entry)
    if ct and ct.group(1) != "assets_document":
        return _result(entry, None, "fail",
                       f"暂不支持 contentType={ct.group(1)} 的链接（当前仅支持教材页 assets_document）")

    cid = parse_content_id(entry)
    if not cid:
        return _result(entry, None, "fail", "无法解析 contentId，请检查链接格式")

    _set("parsing")

    # 1) 获取教材详情 JSON
    try:
        detail = fetch_detail(cid, auth.session, timeout)
    except DetailFetchError as e:
        return _result(entry, None, "fail",
                       f"获取教材信息失败（{e}），链接可能无效或资源已下架")

    # 2) 提取标题与 PDF 候选地址
    title, urls = extract_candidates(detail, cid)
    if item is not None and title:
        item["title"] = title
    if not urls:
        return _result(entry, title, "fail", "未在教材信息中找到 PDF 地址")

    # 3) 目标路径（重名去重 / 已下载跳过）
    meta = extract_metadata(detail)
    base = build_filename(meta, title, flat=flat_name)
    dest, skip = resolve_dest(out_dir, base)
    if skip:
        return _result(entry, title, "skip", f"文件已存在，跳过: {dest}")

    _set("downloading")
    label = f"下载:{base[:14]}"

    # 4) 下载（当前会话，可能匿名或已带令牌）
    try:
        download_file(urls, dest, auth.session, retries, timeout, label,
                      on_progress=on_progress)
        return _result(entry, title, "ok", str(dest))
    except NeedAuthError:
        pass  # 走令牌降级流程
    except DownloadError as e:
        return _result(entry, title, "fail", f"下载失败: {e}")
    except OSError as e:
        return _result(entry, title, "fail", f"文件写入失败: {e}")

    # 5) 401/403 -> 获取令牌后用 X-ND-AUTH 请求头重试
    _log("  -> 平台要求登录鉴权（401/403），尝试获取令牌...")
    had_token = bool(auth.token)
    token = auth.obtain_token(on_log=_log)
    if not token:
        hint = ("界面中\"如何获取登录令牌?\"折叠面板" if not auth.interactive
                else "上方提示或 --help")
        msg = (f"已配置的令牌无效或已过期，请重新获取 x-nd-auth 后重试（见{hint}）" if had_token
               else f"下载需要登录令牌但未提供，已跳过（获取方法见{hint}）")
        return _result(entry, title, "fail", msg)
    auth.apply_token(token)
    try:
        download_file(urls, dest, auth.session, retries, timeout, label,
                      on_progress=on_progress)
        return _result(entry, title, "ok", str(dest))
    except NeedAuthError:
        pass  # 请求头方式仍 401 -> 再试 URL 参数方式
    except DownloadError as e:
        return _result(entry, title, "fail", f"下载失败: {e}")
    except OSError as e:
        return _result(entry, title, "fail", f"文件写入失败: {e}")

    # 6) 备选方案：URL 追加 accessToken 查询参数
    try:
        variant_urls = [append_access_token(u, token) for u in urls]
        download_file(variant_urls, dest, auth.session, retries, timeout, label,
                      on_progress=on_progress)
        return _result(entry, title, "ok", str(dest))
    except NeedAuthError:
        return _result(entry, title, "fail", "令牌无效或已过期，请重新获取 x-nd-auth 后重试")
    except DownloadError as e:
        return _result(entry, title, "fail", f"下载失败: {e}")
    except OSError as e:
        return _result(entry, title, "fail", f"文件写入失败: {e}")



#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
书到了（ShudaoLe / Book Arrived）· 国家中小学智慧教育平台教材 PDF 下载工具
======================================================================

功能：
  1. 根据教材详情页链接（或 contentId）自动解析教材真实 PDF 地址并下载
  2. 支持单个及批量输入：命令行参数、文本文件（-f）、交互粘贴（无参数运行）
  3. 请求携带浏览器请求头（User-Agent / Referer 等），降低被拦截概率
  4. 下载文件以教材名称命名，保存到指定文件夹（-o，默认 ./downloads）
  5. 异常处理、下载失败重试（退避 + 多线路轮换）、下载进度显示

用法示例：
  python smartedu_downloader.py "https://basic.smartedu.cn/tchMaterial/detail?contentType=assets_document&contentId=xxxx&catalogType=tchMaterial&subCatalog=tchMaterial"
  python smartedu_downloader.py <链接或ID1> <链接或ID2> -o "D:/教材"
  python smartedu_downloader.py -f links.txt -o ./downloads
  python smartedu_downloader.py                 # 不带参数 -> 交互模式，粘贴多行链接后空行结束

图形界面（网页版，自动打开浏览器）：
  python smartedu_downloader_gui.py             # 需与本文件放在同一目录

关于登录令牌（下载 PDF 通常需要）：
  平台对 PDF 下载启用了登录鉴权，匿名请求一般返回 401。获取方法：
    1) 浏览器登录 https://basic.smartedu.cn
    2) 按 F12 打开开发者工具 -> 网络(Network) 标签 -> 过滤框输入 pdf
    3) 刷新页面，点击列表中的 pdf.pdf 请求
    4) 在 Request Headers(请求标头) 中找到 x-nd-auth，复制其完整值
  令牌可通过 --token 参数、环境变量 SMARTEDU_TOKEN、脚本同目录 token.txt 提供；
  也可在工具提示时交互粘贴（将自动保存到 token.txt 供下次复用）。

免责声明：本工具仅供个人学习研究使用，教材资源版权归国家中小学智慧教育平台所有。
"""

import argparse
import ipaddress
import json
import os
import re
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

# ---------- 控制台 UTF-8 兼容（Windows GBK 控制台不至于乱码崩溃） ----------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False  # 缺少 tqdm 时使用内置百分比进度显示

# ---------- 常量 ----------
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")

# 教材详情 JSON（静态 CDN，无需登录；多端点互为镜像，逐个尝试）
DETAIL_ENDPOINTS = [
    "https://s-file-1.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/{cid}.json",
    "https://s-file-2.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/{cid}.json",
    "https://s-file-3.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/{cid}.json",
    "https://s-file-1.ykt.cbern.com.cn/zxx/ndrs/tch_material/details/{cid}.json",   # 旧版接口兜底
    "https://s-file-2.ykt.cbern.com.cn/zxx/ndrs/tch_material/details/{cid}.json",
]

# ti_storages 为空时按固定模式构造 PDF 候选地址（r1/r2/r3 互为镜像）
PDF_URL_TEMPLATE = "https://r{n}-ndr.ykt.cbern.com.cn/edu_product/esp/assets_document/{cid}.pkg/pdf.pdf"

TOKEN_FILE = Path(__file__).resolve().parent / "token.txt"

TOKEN_HELP = (
    "  获取登录令牌的方法:\n"
    "    1) 浏览器登录 https://basic.smartedu.cn\n"
    "    2) 按 F12 打开开发者工具 -> 网络(Network) 标签 -> 过滤框输入 pdf\n"
    "    3) 刷新页面，点击列表中的 pdf.pdf 请求\n"
    "    4) 在 Request Headers(请求标头) 中找到 x-nd-auth，复制其完整值"
)

CONTENT_ID_RE = re.compile(r"contentId=([0-9a-zA-Z\-_]{8,})")
CONTENT_TYPE_RE = re.compile(r"contentType=([\w-]+)")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|\r\n\t]+')


# ---------- 异常类型 ----------
class NeedAuthError(Exception):
    """需要登录令牌（服务器返回 401/403）"""


class BadContentError(Exception):
    """响应内容不是合法 PDF（错误页伪装成 200），不可续传，需整份重来。"""


class IncompleteDownload(Exception):
    """下载未完成（长度不足/续传越界），已保留 .part，下轮可续传。"""


class TransientError(Exception):
    """服务端临时故障（5xx / 429），稍后重试通常可恢复。"""
    """响应内容不是有效 PDF"""


class DownloadError(Exception):
    """下载失败（网络错误、非 200 状态等）"""


class DetailFetchError(Exception):
    """教材详情信息获取失败"""


class CancelledError(Exception):
    """下载被用户主动取消（界面“停止”按钮触发）"""


# ---------- 输入解析 ----------
def parse_content_id(text):
    """从详情页链接或裸 contentId 中提取 contentId；失败返回 None"""
    text = (text or "").strip()
    if UUID_RE.match(text):
        return text.lower()
    m = CONTENT_ID_RE.search(text)
    return m.group(1) if m else None


def load_links_from_file(path):
    """从文本文件读取批量输入：每行一个链接/ID，忽略空行与 # 注释行"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = p.read_text(encoding="gbk", errors="replace")
    entries = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries


def interactive_input():
    """交互模式：粘贴多行链接/ID，空行结束"""
    print("请粘贴教材详情页链接或 contentId（可一次粘贴多行），输入空行后开始下载:")
    entries = []
    while True:
        try:
            line = input().strip()
        except EOFError:
            break
        if not line:
            break
        entries.append(line)
    return entries


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


# ---------- 元数据提取（用于结构化命名） ----------
def extract_metadata(detail):
    """从详情 JSON 提取教材元数据，返回 {phase/grade/subject/publisher/volume}。

    标签维度 tag_list 里的 tag_dimension_id -> tag_name，复用 TAG_DIM_MAP 归一。
    缺失字段返回空字符串，由调用方决定占位或省略。"""
    meta = {"phase": "", "grade": "", "subject": "",
            "publisher": "", "volume": ""}
    for t in detail.get("tag_list") or []:
        dim = TAG_DIM_MAP.get(t.get("tag_dimension_id"))
        if dim and not meta.get(dim):
            meta[dim] = (t.get("tag_name") or "").strip()
    # volume 可能由 semester 标签承载（册次：上册/下册/全一册）
    if not meta["volume"]:
        v = meta.get("semester") or ""
        m = _VOLUME_RE.search(v)
        if m:
            meta["volume"] = m.group(1)
    _normalize_grade_meta(meta)
    return meta


def _normalize_grade_meta(meta):
    """年级值归一：剔除占位值、去掉学段前缀。就地修改。

    目录标签与详情标签同源，两处都用同一规则，避免网页预览与下载落盘
    拿到两套年级写法。"""
    grade = (meta.get("grade") or "").strip()
    if any(p in grade for p in GRADE_PLACEHOLDERS):
        grade = ""
    if len(grade) > 2 and grade[:2] in ("高中", "初中", "小学"):
        grade = grade[2:]
    meta["grade"] = grade
    return meta


def normalize_title(title):
    """把平台原始标题清洗为统一格式的展示名。

    处理四类不一致：
      1. 冗余前缀：修订说明、教科书/教材等「载体段」（只说明书本类别，不是书名）
      2. 分隔符混用：项目符 • 与间隔号 · 统一为 ·
      3. 空格随性：「英语 三年级 上册」与「英语三年级上册」统一为后者
      4. 学制写法：「五•四学制」「五四学制」统一为「五·四学制」

    学制与版本变体等区分性信息一律保留——六三制与五四制是两套不同的书，
    丢掉会让同名教材互相覆盖。"""
    s = (title or "").strip()
    if not s:
        return ""
    # 1) 统一分隔符（先做，后续按 · 切分才可靠）
    s = s.replace("\u2022", "\u00b7").replace("\uff65", "\u00b7")
    s = re.sub(r"\s*·\s*", "·", s)
    # 2) 删除中文字之间的空格；括号与中文之间的空格同样删除
    s = _CJK_SPACE_RE.sub("", s)
    s = _SPACE_BEFORE_PAREN_RE.sub("", s)
    s = _SPACE_AFTER_PAREN_RE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    # 3) 学制写法归一（放在删修订说明之前，括号形态此时最完整）
    s = _SYSTEM_IN_TITLE_RE.sub("（五·四学制）", s)
    # 4) 去掉修订说明前缀（可能叠加多层）
    for _ in range(3):
        s2 = _REVISION_RE.sub("", s).strip()
        if s2 == s:
            break
        s = s2
    # 5) 从头部循环剥离「载体短语」（义务教育教科书/实验教科书/教学指南…）。
    #    特殊学校标识（聋校/盲校/培智）会被保留并前置，不随载体丢弃——
    #    它们是区分同名教材的关键（聋校的道德与法治 ≠ 普通小学的）。
    #    仅当剥离后仍剩实质内容时才丢，避免把整名（如果整名都是载体词）丢空。
    kept_school = ""
    kept_system = ""
    while True:
        m = _CARRIER_HEAD_RE.match(s)
        if not m:
            break
        stripped = s[m.end():]
        if not stripped.strip():
            break           # 只剩载体段，保留（否则整名变空）
        seg = m.group(0)
        sch = m.group("school") or ""
        sch = "培智" if sch == "培智学校" else sch
        if sch and not kept_school:
            kept_school = sch
        # 载体段括号里的学制（五·四学制/六·三学制）是两套不同书的区分点，保留不丢
        sm = _SYSTEM_ANY_RE.search(seg)
        if sm and not kept_system:
            kept_system = sm.group(1)
        s = stripped.strip()
    if kept_school and not s.startswith(kept_school):
        s = kept_school + s
    if kept_system and kept_system not in s:
        s = s + "（%s）" % kept_system
    # 6) 收尾：清理空括号 / 重复间隔号 / 首尾分隔符
    s = _EMPTY_PAREN_RE.sub("", s)
    s = _MULTI_DOT_RE.sub("·", s)
    s = re.sub(r"·\s*(?=·)", "", s)
    s = s.strip("· ").strip()
    return s.strip()


def title_distinct(norm_title, meta):
    """从清洗后的标题里剥离已在结构化字段中出现的值，得到「区分性修饰语」。

    结构化命名拼 学段/特殊学校/科目/版本/学制/年级/册次，标题里真正体现差异的
    「教师用书 篮球」「（简谱）」「选择性必修2 网络基础」等修饰语会补在文件名尾部；
    不补上这段，3672 条教材只会得到 2034 个文件名。已进入文件名结构段的字段
    （学段/学校/科目/版本/学制/年级/册次）会被剥掉，避免与结构段重复。"""
    s = (norm_title or "").strip()
    if not s:
        return ""
    # 只剥离「已进入文件名模板结构段」的字段；audience(教师/学生用书)、
    # editor(主编)、module 等不在结构段里，一律保留——它们正是同名教材的差别所在。
    for key in ("phase", "school", "grade", "volume", "subject",
                "publisher", "system"):
        v = (meta.get(key) or "").strip()
        if not v:
            continue
        s = s.replace("（%s）" % v, "·").replace("(%s)" % v, "·")
        s = s.replace(v, "·")
    s = _EMPTY_PAREN_RE.sub("", s)
    s = re.sub(r"[·\s]{2,}", "·", s)
    s = s.strip("· ").strip()
    s = re.sub(r"^[（(]\s*[）)]|[（(]\s*[）)]$", "", s)
    return s.strip("· ").strip()


def build_filename(meta, title, flat=False):
    """按元数据构造文件名（不含扩展名）。

    flat=True 时只用清洗后的书名（历史行为）；否则用
    「{学段}[{特殊学校}]{科目}_{版本}[_{学制}]_{年级}{册次}[_{区分语}]」模板，
    缺失字段自动省略。特殊学校（聋校/盲校/培智）与学制（五·四/六·三学制）
    若元数据没提供，则从清洗后的标题里补提——它们是同名教材的区分点。
    例: 小学语文_统编版_一年级上册
        初中体育与健康_人教版_教师用书篮球（全一册）
        聋校数学_人教版_二年级下册
        小学道德与法治_统编版_五·四学制一年级上册
    """
    clean = normalize_title(title) if title else ""
    if flat:
        return sanitize_filename(clean or (title or ""))

    # 用元数据 + 标题共同确定各结构段；标题里带了但元数据缺失的 school/system 补进来
    eff = dict(meta or {})
    if not eff.get("school"):
        for kw in ("聋校", "盲校", "培智"):
            if clean.startswith(kw) or ("" + kw) in clean[:4]:
                eff["school"] = kw
                break
    if not eff.get("system"):
        sm = _SYSTEM_ANY_RE.search(clean)
        if sm:
            eff["system"] = sm.group(1)

    parts = []
    # 前缀段: 学段 + 特殊学校 + 科目（如 盲校语文 / 聋校数学）
    prefix = "".join(x for x in (eff.get("phase", ""),
                                 eff.get("school", ""),
                                 eff.get("subject", "")) if x)
    if prefix:
        parts.append(prefix)

    # 版本段（含变体，如 人教版A版）
    pub = eff.get("publisher", "")
    if pub:
        parts.append(pub)

    # 学制段: 五·四学制 / 六·三学制 是两套不同的书，放独立段避免与六三制同名覆盖
    system = eff.get("system", "")
    if system:
        parts.append(system)

    # 年级册次段: 年级 + 册次 连续拼
    tail = "".join(x for x in (eff.get("grade", ""), eff.get("volume", "")) if x)
    if tail:
        parts.append(tail)

    # 区分语：标题里未被上述结构段覆盖的修饰部分（模块名/教师用书/简谱…）
    distinct = title_distinct(clean, eff)
    if distinct and distinct not in "".join(parts):
        parts.append(distinct)

    name = "_".join(p for p in parts if p)
    # 元数据全缺 -> 回退清洗后的书名
    return sanitize_filename(name or clean or title)


# ---------- 文件名与本地路径 ----------
def sanitize_filename(name):
    """清理 Windows 非法字符、首尾空格与点，限制长度"""
    name = ILLEGAL_CHARS_RE.sub("_", (name or "").strip()).strip(" .")
    return (name[:120] or "未命名教材")


def resolve_dest(out_dir, base_name):
    """确定目标文件路径。返回 (路径, 是否跳过)；
    已存在同名且体积 >1MB 的文件视为已下载完成，跳过。
    base_name 由远端书名生成，这里显式校验最终路径不会越出 out_dir。"""
    out_root = out_dir.resolve()
    candidate = out_dir / f"{base_name}.pdf"
    i = 1
    while True:
        if not candidate.resolve().is_relative_to(out_root):
            raise DownloadError(f"保存路径越界，拒绝写入: {candidate}")
        if not candidate.exists():
            return candidate, False
        if candidate.stat().st_size > 1_000_000:
            return candidate, True
        i += 1
        candidate = out_dir / f"{base_name}({i}).pdf"


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
            if not tmp.resolve().is_relative_to(dest.resolve().parent):
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
    """令牌来源优先级: --token 参数 > 环境变量 SMARTEDU_TOKEN > token.txt 文件"""
    if cli_token and cli_token.strip():
        return cli_token.strip()
    env = (os.environ.get("SMARTEDU_TOKEN") or "").strip()
    if env:
        return env
    if TOKEN_FILE.exists():
        try:
            t = TOKEN_FILE.read_text(encoding="utf-8", errors="replace").strip()
            if t:
                return t
        except OSError:
            pass
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
        log = on_log or print
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
                log(f"  已将令牌保存到 {TOKEN_FILE}（下次运行自动使用）")
            except OSError as e:
                log(f"  令牌保存失败: {e}")
        return token


# ---------- 教材目录（按 学段/年级/科目/版本 自动检索） ----------
CATALOG_PARTS = (100, 101, 102, 103)  # 平台教材目录分片（实测 104 起已下线）
CATALOG_URL = "https://s-file-{n}.ykt.cbern.com.cn/zxx/ndrs/resources/tch_material/part_{p}.json"
CATALOG_CACHE = Path(__file__).resolve().parent / "catalog_cache.json"
CATALOG_TTL = 7 * 86400  # 目录缓存有效期（秒）
# 平台标签维度 -> 本工具字段名
TAG_DIM_MAP = {
    "zxxxd": "phase",       # 学段（小学/初中/高中）
    "zxxnj": "grade",       # 年级
    "zxxxk": "subject",     # 科目
    "zxxbb": "publisher",   # 版本（出版社）
    "zxxcc": "semester",    # 册别（上册/下册/全一册）
}


# 缓存结构版本：规范化规则变更时递增，旧缓存会被就地重算而非重新下载
CATALOG_SCHEMA = 5


def fetch_catalog_index(force=False, timeout=60, on_log=None):
    """获取全部教材目录索引，返回规范化后的列表，每项形如:
        {"id", "title", "phase", "system", "grade", "subject", "publisher",
         "editor", "pub_variant", "volume", "module", "audience", "school",
         "kind", 以及 *_raw 原始标签值}
    优先使用本地缓存 catalog_cache.json（7 天有效）；force=True 强制重新下载。
    注意: 首次下载约 40MB（4 个分片），耗时取决于网速。"""
    log = on_log or (lambda m: None)
    if not force and CATALOG_CACHE.exists():
        try:
            data = json.loads(CATALOG_CACHE.read_text(encoding="utf-8"))
            if data.get("items") and time.time() - data.get("fetched_at", 0) < CATALOG_TTL:
                items = data["items"]
                if data.get("schema") != CATALOG_SCHEMA:
                    log("缓存格式已升级，正在就地重新整理...")
                    items = normalize_catalog(items)
                    _write_catalog_cache(items, log)
                log(f"使用本地目录缓存（共 {len(items)} 条教材）")
                return items
        except (OSError, ValueError):
            pass

    items, seen = [], set()
    for p in CATALOG_PARTS:
        data, last_err = None, None
        for host in (1, 2, 3):  # s-file-1/2/3 互为镜像
            try:
                resp = requests.get(
                    validate_public_http_url(CATALOG_URL.format(n=host, p=p)),
                    headers={"User-Agent": UA}, timeout=timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    break
                last_err = f"HTTP {resp.status_code}"
            except requests.RequestException as e:
                last_err = type(e).__name__
        if data is None:
            raise DownloadError(f"目录分片 part_{p} 获取失败（{last_err}），请稍后重试")
        for e in data:
            cid = e.get("id")
            title = (e.get("title") or "").strip()
            if not cid or cid in seen or not title:
                continue
            seen.add(cid)
            entry = {"id": cid, "title": title}
            for t in e.get("tag_list") or []:
                dim = TAG_DIM_MAP.get(t.get("tag_dimension_id"))
                if dim and not entry.get(dim):
                    entry[dim] = (t.get("tag_name") or "").strip()
            items.append(entry)
        log(f"目录分片 part_{p} 完成，累计 {len(items)} 条教材")

    items = normalize_catalog(items)
    _write_catalog_cache(items, log)
    return items


def _write_catalog_cache(items, log):
    """把目录写入磁盘缓存（失败静默——缓存只是加速手段）。"""
    try:
        CATALOG_CACHE.write_text(
            json.dumps({"fetched_at": time.time(), "schema": CATALOG_SCHEMA,
                        "items": items}, ensure_ascii=False),
            encoding="utf-8")
        log(f"目录已缓存到 {CATALOG_CACHE.name}（7 天内无需重新下载）")
    except OSError:
        pass


# ---------- 目录规范化：把平台原始标签整理为相互正交的维度 ----------
#
# 平台原始标签存在三类问题，直接拿去做筛选会漏检/误检：
#   1) 维度污染：年级里混着"教师用书""中国历史"，版本里嵌着"（主编：曹理）"
#   2) 命名不一："艺术·音乐" 与 "音乐"、"人教A版" 与 "人教版" 实为同一事物
#   3) 标签缺失：约 650 条体育与健康教材完全无标签，同时混有"第N课"等课件
# 规范化把污染值归位、统一命名、从书名补全缺失维度，并区分教材/非教材资源。

# 被平台错放进"年级"维度的值 -> (应属维度, 取值)
GRADE_MISPLACED = {
    "教师用书": ("audience", "教师用书"),
    "学生用书": ("audience", "学生用书"),
    "学生读本": ("audience", "学生读本"),
    "人工智能专册": ("module", "人工智能专册"),
    "中国历史": ("subject", "中国历史"),
    "世界历史": ("subject", "世界历史"),
}
# 年级的口语/简写 -> 标准取值
GRADE_CANON = {
    "高中年级": "高中（不分年级）",
    "高一年级": "高中一年级",
    "高二年级": "高中二年级",
    "高三年级": "高中三年级",
}
# 科目规范化：合并同一学科在不同课标版本下的命名
SUBJECT_CANON = {
    "艺术·音乐": ("音乐", "2022年版课标"),
    "艺术·美术": ("美术", "2022年版课标"),
    "艺术·舞蹈/影视/戏剧": ("艺术", "舞蹈/影视/戏剧"),
    "语文·书法练习指导": ("语文", "书法练习指导"),
    "英语（三年级起点）": ("英语", "三年级起点"),
    "地理图册": ("地理", "图册"),
}
# 版本规范化：无括号形式的变体拆分
PUBLISHER_CANON = {
    "人教A版": ("人教版", "A版"),
    "人教B版": ("人教版", "B版"),
}
# 年级 -> 学段（年级存在而学段缺失时据此补全）
GRADE_TO_PHASE = {
    "一年级": "小学", "二年级": "小学", "三年级": "小学",
    "四年级": "小学", "五年级": "小学", "六年级": "小学",
    "七年级": "初中", "八年级": "初中", "九年级": "初中",
    "高中（不分年级）": "高中", "高中一年级": "高中",
    "高中二年级": "高中", "高中三年级": "高中",
}
# 排序权重：保证下拉选项按认知顺序而非字典序排列
PHASE_ORDER = ["小学", "初中", "高中", "特殊教育"]
GRADE_ORDER = ["一年级", "二年级", "三年级", "四年级", "五年级", "六年级",
               "七年级", "八年级", "九年级", "七至九年级",
               "高中一年级", "高中二年级", "高中三年级", "高中（不分年级）"]
VOLUME_ORDER = ["上册", "下册", "全一册"]

_PAREN_RE = re.compile(r"[（(]([^）)]*)[）)]")      # 全角/半角括号内容
_EDITOR_RE = re.compile(r"^\s*主编\s*[:：]\s*(.+?)\s*$")
_VOLUME_RE = re.compile(r"(上册|下册|全一册)")
_GRADE_IN_TITLE_RE = re.compile(
    r"(一年级|二年级|三年级|四年级|五年级|六年级|七年级|八年级|九年级"
    r"|七至九年级|高一|高二|高三)")
_SPECIAL_SCHOOL_RE = re.compile(r"(聋校|盲校|培智|特殊教育)")
# 非教材资源（课件/课时/活动/指南），默认不参与教材筛选
_RESOURCE_RE = re.compile(
    r"^(第\s*\d+\s*课|活动\s*\d+|项目\s*\d+|单元\s*\d+|主题\s*\d+)"
    r"|教学指南|使用说明|课件|教学设计|教学案例")

# ---------- 标题清洗 ----------
# 平台标题混用「间隔号 ·」「项目符 •」且带大量冗余前缀，直接展示/命名会格式不一：
#   （根据2022年版课程标准修订）义务教育教科书•艺术•音乐（简谱）八年级下册
#   → 艺术·音乐（简谱）八年级下册
_REVISION_RE = re.compile(r"^[（(]\s*根据\s*\d{4}\s*年版课程标准修订\s*[）)]\s*")
# 学制标注写法不一（五•四学制 / 五·四学制 / 五四学制），统一为间隔号写法
_SYSTEM_IN_TITLE_RE = re.compile(r"[（(]\s*五\s*[•·]?\s*四\s*学制\s*[）)]")
# 从任意文本里抽出学制标识（用于载体段剥离时保留五·四/六·三学制这一区分点）
_SYSTEM_ANY_RE = re.compile(r"[（(]?\s*(五\s*[•·]?\s*四学制|六\s*[•·]?\s*三学制)\s*[）)]?")
# 头部「载体短语」：只说明书本类别（义务教育教科书/实验教科书/教学指南…），
# 不是书名本身。可能带学制等括号后缀，如「义务教育教科书（五·四学制）·体育…」。
# 整体匹配后从头部循环剥离，直到遇到真正的书名段为止。
# 特殊学校标识（聋校/盲校/培智）单独捕获（group 1），剥离载体后补回，不随载体丢弃。
_CARRIER_HEAD_RE = re.compile(
    r"^(?P<school>(?:聋校|盲校|培智)(?:学校)?)?"
    r"(?:(?:义务教育|普通高中|特殊教育|高中|九年义务教育)"
    r"(?:（[^）)]*）)?)?"
    r"(?=[\u4e00-\u9fffA-Za-z0-9]*?(?:教科书|实验教科书|教材|教学指南))"
    r"[\u4e00-\u9fffA-Za-z0-9（）()·•]*?(?:教科书|实验教科书|教材|教学指南)"
    r"(?:\s*[（(][^）)]*[）)])?\s*[·•]?\s*")
_MULTI_DOT_RE = re.compile(r"[·]{2,}")
_CJK_SPACE_RE = re.compile(          # 中文之间的空格（中文书名不加空格）
    r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])")
_SPACE_BEFORE_PAREN_RE = re.compile(r"(?<=[\u4e00-\u9fff])\s+(?=[（(])")
_SPACE_AFTER_PAREN_RE = re.compile(r"(?<=[）)])\s+(?=[\u4e00-\u9fff])")
_EMPTY_PAREN_RE = re.compile(r"[（(]\s*[·\s]*[）)]")
# 年级占位值：不指向真实年级，归入年级维度会让文件名出现「高中（不分年级）」这类冗余
GRADE_PLACEHOLDERS = ("不分年级", "通用", "全学段")


def _split_parens(text):
    """拆出字符串中的全部括号内容，返回 (主体, [括号内容...])。"""
    parts = _PAREN_RE.findall(text)
    main = _PAREN_RE.sub("", text).strip()
    return main, parts


def normalize_catalog(items):
    """把目录条目规范化为正交维度（原地补充字段，返回同一列表）。

    新增字段: phase/system/grade/subject/publisher/editor/variant/
              volume/module/audience/school/kind；原始值保留在 *_raw。
    规范后的字段互不重叠：任一取值只可能出现在它所属的维度里。
    幂等：始终从 *_raw 原始标签重算，可反复执行；升级规则后旧缓存无需重新下载。"""
    for it in items:
        # 标题统一以原始值为输入（title_raw 存在时优先），保证重复规范化结果一致；
        # 旧缓存没有该字段时退回 title（即当时的原始值），升级规则后无需重新下载。
        title = (it.get("title_raw") or it.get("title") or "").strip()
        it["title_raw"] = title
        # 一律以原始标签为输入（*_raw 存在时优先），保证重复规范化结果一致
        raw = lambda k: (it.get(k + "_raw") or it.get(k) or "").strip()

        # --- 1. 版本：拆出主编与变体，去掉"配套"前缀 ---
        pub_raw = raw("publisher")
        pub, pub_parts = _split_parens(pub_raw)
        editor, variants = "", []
        for p in pub_parts:
            m = _EDITOR_RE.match(p)
            if m:
                editor = m.group(1).replace("，", "、").replace(",", "、")
            elif p:
                variants.append(p)
        if pub in PUBLISHER_CANON:
            pub, v = PUBLISHER_CANON[pub]
            variants.insert(0, v)
        if pub.startswith("配套"):
            variants.insert(0, "配套")
            pub = pub[2:]
        it["publisher_raw"], it["publisher"] = pub_raw, pub.strip()
        it["editor"] = editor
        it["pub_variant"] = "、".join(variants)

        # --- 2. 科目：合并同一学科在不同课标版本下的命名 ---
        subj_raw = raw("subject")
        subj, _subj_note = SUBJECT_CANON.get(subj_raw, (subj_raw, ""))
        it["subject_raw"], it["subject"] = subj_raw, subj

        # --- 3. 年级：脏值归位 + 写法统一 ---
        grade_raw = raw("grade")
        grade = GRADE_CANON.get(grade_raw, grade_raw)
        audience = it.get("audience") or ""
        if grade in GRADE_MISPLACED:                 # 错放的年级 -> 归位
            dim, val = GRADE_MISPLACED[grade]
            if dim == "subject" and not subj:
                it["subject"] = val
            elif dim == "audience" and not audience:
                audience = val
            elif dim == "module":
                it["module"] = val
            grade = ""
        # 占位年级（"高中（不分年级）"等）与学段前缀写法归一，
        # 与详情路径共用同一规则（_normalize_grade_meta）
        grade = _normalize_grade_meta({"grade": grade})["grade"]
        it["grade_raw"], it["grade"] = grade_raw, grade
        it["audience"] = audience

        # --- 4. 学段：拆出学制（五·四 / 六·三）---
        phase_raw = raw("phase")
        system = ""
        if "五•四学制" in phase_raw or "五四学制" in phase_raw:
            phase, system = phase_raw.split("（")[0].strip(), "五·四学制"
        elif "六•三学制" in phase_raw:
            phase, system = phase_raw.split("（")[0].strip(), "六·三学制"
        else:
            phase = phase_raw
        it["phase_raw"], it["phase"] = phase_raw, phase
        it["system"] = system

        # --- 5. 册别：拆为册次(volume) + 模块(module) ---
        sem_raw = (it.get("semester_raw") or it.get("semester") or "").strip()
        m = _VOLUME_RE.search(sem_raw)
        if m:
            volume = m.group(1)
            module = _VOLUME_RE.sub("", sem_raw).strip(" （）()·•")
        else:
            volume, module = "", sem_raw
        # 册别里可能带着年级（如"一年级（全一册）"），年级归年级维度，模块不重复承载
        if _GRADE_IN_TITLE_RE.fullmatch(module):
            module = ""
        it["semester_raw"], it["volume"], it["module"] = sem_raw, volume, module

        # --- 6. 资源类型：教材 vs 课件等非教材资源 ---
        it["kind"] = "resource" if _RESOURCE_RE.search(title) else "textbook"

        # --- 7. 从书名补全缺失维度（平台未打标签的条目）---
        if not it["system"] and ("五•四学制" in title or "五四学制" in title):
            it["system"] = "五·四学制"
        if not it["grade"]:
            gm = _GRADE_IN_TITLE_RE.search(title)
            if gm:
                it["grade"] = GRADE_CANON.get(gm.group(1), gm.group(1))
        if not it["audience"]:
            if "教师用书" in title:
                it["audience"] = "教师用书"
            elif "学生用书" in title:
                it["audience"] = "学生用书"
        sm = _SPECIAL_SCHOOL_RE.search(title)
        it["school"] = sm.group(1) if sm else ""
        if not it["subject"]:
            for kw in ("体育与健康", "道德与法治", "信息技术", "信息科技",
                       "语文", "数学", "英语", "历史", "地理", "音乐", "美术"):
                if kw in title:
                    it["subject"] = kw
                    break
        if not it["phase"]:
            if "普通高中" in title or "高中" in title:
                it["phase"] = "高中"
            elif it["school"] or "特殊教育" in title:
                it["phase"] = "特殊教育"
            elif "小学" in title:
                it["phase"] = "小学"
            else:
                it["phase"] = GRADE_TO_PHASE.get(it["grade"], "")
        if not it["volume"]:
            vm = _VOLUME_RE.search(title)
            if vm:
                it["volume"] = vm.group(1)

        # --- 8. 展示名：清洗原始标题（冗余前缀/分隔符/空格/学制写法）---
        # 原始值留在 title_raw，保证本函数幂等且搜索仍能命中原始书名。
        it["title"] = normalize_title(title)

        it["_norm"] = True
    return items


# ---------- 目录搜索 ----------

# 年级口语别名 -> 平台标签值（平台用"七年级"而非"初一"）
GRADE_ALIASES = {
    "小一": "一年级", "小二": "二年级", "小三": "三年级",
    "小四": "四年级", "小五": "五年级", "小六": "六年级",
    "初一": "七年级", "初1": "七年级",
    "初二": "八年级", "初2": "八年级",
    "初三": "九年级", "初3": "九年级",
    "高一": "高中一年级", "高二": "高中二年级", "高三": "高中三年级",
}

# 可参与关键词检索的字段（按展示优先级排列，用于定向搜索与字段加权）
SEARCH_FIELDS = ("title", "title_raw", "subject", "subject_raw", "publisher",
                 "publisher_raw", "editor", "grade", "grade_raw",
                 "phase", "volume", "module", "school", "audience")
# 关键词里 field:value 形式支持的字段名
FIELD_ALIASES = {
    "书名": "title", "标题": "title", "原名": "title_raw", "原始书名": "title_raw",
    "科目": "subject", "学科": "subject",
    "版本": "publisher", "出版社": "publisher", "主编": "editor",
    "作者": "editor", "年级": "grade", "学段": "phase", "册": "volume",
    "模块": "module", "学校": "school", "用途": "audience",
}

_CN_DIGITS = {"零": "0", "一": "1", "二": "2", "三": "3", "四": "4",
              "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
_DIGIT_CN = {v: k for k, v in _CN_DIGITS.items() if v != "10"}


def _norm_text(s):
    """归一化检索文本：全角转半角、小写、去掉常见分隔符。"""
    s = (s or "").strip().lower()
    out = []
    for ch in s:
        code = ord(ch)
        if code == 12288:                       # 全角空格
            out.append(" ")
        elif 65281 <= code <= 65374:            # 全角字符 -> 半角
            out.append(chr(code - 65248))
        else:
            out.append(ch)
    return "".join(out).replace("·", "").replace("•", "").replace("•", "")


def _variants(s):
    """生成文本的等价写法，用于中文数字/阿拉伯数字互通（一 ↔ 1）。"""
    base = _norm_text(s)
    forms = {base}
    if any(c in _CN_DIGITS for c in base):
        forms.add("".join(_CN_DIGITS.get(c, c) for c in base))
    if any(c.isdigit() for c in base):
        forms.add("".join(_DIGIT_CN.get(c, c) for c in base))
    return forms


def _tokenize(query):
    """切分检索词：支持空格分词、"精确短语" 与 -排除词。

    返回 (必须命中的词列表, 必须排除的词列表)；每项是 (字段或None, 文本)。"""
    must, must_not = [], []
    for raw in re.findall(r'-?"[^"]+"|\S+', query or ""):
        negate = raw.startswith("-")
        token = raw[1:] if negate else raw
        token = token.strip('"').strip()
        if not token:
            continue
        field = None
        fm = re.match(r"^([^:：]{1,6})[:：](.+)$", token)
        if fm:                                   # 支持 科目:数学 这类定向检索
            key = FIELD_ALIASES.get(_norm_text(fm.group(1)), _norm_text(fm.group(1)))
            if key in SEARCH_FIELDS and fm.group(2).strip():
                field, token = key, fm.group(2).strip()
        (must_not if negate else must).append((field, token))
    return must, must_not


def match_keyword(item, query):
    """判断条目是否命中关键词。多词 AND、引号短语、-排除、字段定向。"""
    must, must_not = _tokenize(query)
    if not must and not must_not:
        return True

    def hit(field, text):
        wanted = _variants(text)
        for f in ((field,) if field else SEARCH_FIELDS):
            value = _norm_text(item.get(f))
            if not value:
                continue
            for w in wanted:
                if w and w in value:
                    return True
        return False

    return (all(hit(f, t) for f, t in must)
            and not any(hit(f, t) for f, t in must_not))


def detail_page_url(cid):
    """由 contentId 构造教材详情页链接（复用既有解析下载流程）。"""
    return ("https://basic.smartedu.cn/tchMaterial/detail"
            "?contentType=assets_document&contentId=" + cid +
            "&catalogType=tchMaterial&subCatalog=tchMaterial")


# 参与筛选的维度：下拉字段名 -> 排序表（有排序表的按认知顺序，否则按频次）
# 顺序即界面展示顺序，按人的检索路径排列：先定学段，再定年级、科目，
# 版本是"被动识别"属性（多数人看到结果才知道自己用的是哪版），故靠后。
FILTER_DIMS = ("phase", "grade", "subject", "publisher", "volume",
               "audience", "system", "school", "editor", "module")
DIM_ORDERS = {"phase": PHASE_ORDER, "grade": GRADE_ORDER, "volume": VOLUME_ORDER}
DIM_LABELS = {
    "phase": "学段", "publisher": "版本", "grade": "年级", "subject": "科目",
    "volume": "册次", "audience": "用途", "system": "学制", "school": "学校类型",
    "editor": "主编", "module": "模块",
}
# 年级口语别名也用于维度匹配（--grade 初一 == 七年级）
DIM_ALIASES = {"grade": GRADE_ALIASES}

# 网页界面启用的维度白名单（按展示顺序）。后端计算的维度很多，但并非全部适合作为
# 下拉展示；此白名单同时供 GUI 渲染下拉与 CLI 参考，避免前后端各维护一份规则。
# 设为 None 表示启用 FILTER_DIMS 中的全部维度。
ENABLED_DIMS = ("phase", "grade", "subject", "publisher", "volume")

# 当前完整支持的筛选范围。phase 范围外的取值作为占位项禁用；版本维度用「可见分组」
# 模型，后端只返回可见分组，故 publisher 无需在此列（其余版本一律隐藏）。
# 以后要启用其它版本/学段，往 PUB_GROUPS / SUPPORTED_SCOPE 加即可。CLI 与 GUI 共用。
SUPPORTED_SCOPE = {
    "phase": ["小学"],
}
# 默认筛选：某维度限定范围且只剩一个可选值时，进入界面就替用户选中。
# 否则首屏是"全部学段"下几千条混杂结果，而其它学段又因禁选而点不动，
# 用户得自己先想明白"原来只开了小学"。
DEFAULT_FILTERS = {d: v[0] for d, v in SUPPORTED_SCOPE.items() if len(v) == 1}

# 版本下拉可见项：显示名 -> 覆盖的真实 publisher 标签集合（其余标签整体隐藏）。
# 「人教版系」 = 人教版(数学/英语等) + 统编版(语文/道德与法治) + 人教鄂教版(科学)，
# 平台把这三套主科标成不同标签，故并成一组，选它即能下到语文/道法/科学。
PUB_GROUPS = {
    "人教版系": ["人教版", "统编版", "人教鄂教版"],
    "沪教版": ["沪教版"],
}
# 分组在下拉里的显示名（optgroup 标题）；未列出的直接用组名
PUB_GROUP_LABELS = {
    "人教版系": "人教系列",
}
# 版本排序权重：按 PUB_GROUPS 的声明顺序展开，未知版本排最后
PUB_ORDER = [tag for tags in PUB_GROUPS.values() for tag in tags]
# 科目排序权重：主科在前，符合"先找主科"的检索习惯
SUBJECT_ORDER = ["语文", "数学", "英语", "道德与法治", "科学", "体育与健康",
                 "音乐", "美术", "信息技术", "信息科技", "艺术", "综合实践",
                 "历史", "地理", "物理", "化学", "生物"]

# 科目与版本同样按认知顺序排：若只按出现频次排，最常用的语文/数学会被
# 音乐/美术这类教材数量多的科目顶到后面，与人的检索预期相反。
DIM_ORDERS["subject"] = SUBJECT_ORDER
DIM_ORDERS["publisher"] = PUB_ORDER


def publisher_label(tag):
    """真实 publisher 标签 -> 可见分组显示名；不在任何可见组的返回 None（应隐藏）。"""
    for name, tags in PUB_GROUPS.items():
        if tag in tags:
            return name
    return None


def expand_publisher_filter(pub):
    """把 publisher 分组显示值展开为组内真实标签列表（用于筛选谓词）。

    传入 None/空 -> 返回 None（不限版本）；分组名 -> 组内标签；真实标签 -> 自身。"""
    pub = (pub or "").strip()
    if not pub:
        return None
    tags = PUB_GROUPS.get(pub)
    if tags:
        return list(tags)
    return [pub]


def publisher_facets(raw_facets):
    """把真实 publisher 标签整理为「分组 + 成员」两级候选。

    为什么要两级：平台的版本标签是分裂的——语文标"统编版"、数学标"人教版"、
    科学标"人教鄂教版"，可用户心智里它们都是"人教的"。只给合并后的分组，
    想精确下"统编版语文"的人找不到入口；只给真实标签，想下全套人教的人
    得选三次。两级并存才能同时容纳"整套选"与"精确选"两种心智。

    返回 [{value, count, group, is_group}]；group 为 None 表示顶层选项。
    组内只有一个标签时不做分组，避免出现"分组 + 唯一成员"的冗余两层。"""
    counts = {}
    for f in raw_facets:
        if publisher_label(f["value"]):
            counts[f["value"]] = counts.get(f["value"], 0) + f["count"]

    out = []
    for name, tags in PUB_GROUPS.items():
        members = [t for t in tags if counts.get(t)]
        if not members:
            continue
        total = sum(counts[t] for t in members)
        if len(members) > 1:
            group = PUB_GROUP_LABELS.get(name, name)
            out.append({"value": name, "count": total,
                        "group": group, "is_group": True})
            for t in members:
                out.append({"value": t, "count": counts[t],
                            "group": group, "is_group": False})
        else:
            out.append({"value": members[0], "count": total,
                        "group": None, "is_group": False})
    return out


def _rank(order, value):
    """取值 -> 排序序号：在排序表内的按表序靠前，表外的按字典序靠后。"""
    try:
        return (0, order.index(value), "")
    except ValueError:
        return (1, 0, value or "")


def _cognitive_key(item):
    """目录结果排序键：学段 → 年级 → 科目 → 版本 → 册次 → 书名。

    目录原始顺序是平台分片抓取顺序，同一年级的教材会分散在上千行里，人眼
    无法定位。按认知顺序排完后，同年级同科目的教材自然聚在一起。"""
    return (
        _rank(PHASE_ORDER, item.get("phase") or ""),
        _rank(GRADE_ORDER, item.get("grade") or ""),
        _rank(SUBJECT_ORDER, item.get("subject") or ""),
        _rank(PUB_ORDER, item.get("publisher") or ""),
        _rank(VOLUME_ORDER, item.get("volume") or ""),
        item.get("title") or "",
    )


def dim_match(item, dim, query):
    """单个维度是否命中。空查询恒真；空值条目在指定该维度时不命中。

    匹配采用「规范化后子串包含」：查询词必须是维度取值的前缀/子串
    （如"统编"命中"统编版"，"小学"命中"小学（五·四学制）"），
    反向（值包含在查询词里）不再命中，避免过宽误检；需要别名走 DIM_ALIASES。"""
    query = (query or "").strip()
    if not query:
        return True
    aliases = DIM_ALIASES.get(dim, {})
    query = aliases.get(query, query)
    value = (item.get(dim) or "").strip()
    if not value:
        return False
    q, v = _norm_text(query), _norm_text(value)
    return q in v


def search_catalog(items, keyword=None, include_resources=False, sort=True,
                   **filters):
    """在规范化目录上做多维度筛选 + 关键词检索。

    filters 支持 phase/publisher/grade/subject/volume/audience/
    system/school/editor/module（未传或空值表示不限）。
    publisher 支持分组名（如「人教版系」）或真实标签（如「人教版」「人教」），
    分组名会展开为组内任一标签命中；其它维度按前缀/子串匹配。
    默认只返回教材(kind=textbook)；include_resources=True 时包含课件等资源。
    sort=True（默认）按认知顺序排序，便于人眼浏览；级联统计用 sort=False 省开销。"""
    active = {k: (v or "").strip() for k, v in filters.items()
              if k in FILTER_DIMS and (v or "").strip()}
    # publisher 分组名展开为组内标签（OR 命中），其余维度保持 dim_match 子串语义
    pub_tags = expand_publisher_filter(active.get("publisher")) if "publisher" in active else None
    rest = {d: q for d, q in active.items() if d != "publisher"}
    out = []
    for it in items:
        if not include_resources and it.get("kind") != "textbook":
            continue
        if pub_tags is not None and not _item_in_publishers(it, pub_tags):
            continue
        if not all(dim_match(it, d, q) for d, q in rest.items()):
            continue
        if keyword and not match_keyword(it, keyword):
            continue
        out.append(it)
    if sort:
        out.sort(key=_cognitive_key)
    return out


def _item_in_publishers(item, tags):
    """item 的 publisher 是否命中 tags（None 恒真）。

    分组名展开后是组内真实标签，此时"组内任一命中"即用 dim_match 逐个试；
    用户直接写标签片段（如 `--publisher 人教`）时同样走子串语义，从而
    命中"人教版"/"人教鄂教版"。若这里退化成精确相等，简写筛选会静默失效。"""
    if not tags:
        return True
    return any(dim_match(item, "publisher", t) for t in tags)


def catalog_facets(items, filters=None, keyword=None, include_resources=False):
    """级联联动：返回在"除本维度外其它条件"下，各维度的可选值与命中数。

    例如已选 小学+人教版 后，年级只会出现这两个条件下真实存在的年级。
    返回值形如 {"phase": [{"value": "小学", "count": 89}, ...], ...}，
    每个维度的取值互不重复（已按规范化后的字段去重）。

    关键：每个维度的候选值必须在「排除自身选择」的子集上统计。否则选中
    某维度后，该维度的候选就只剩下当前选中值，用户无法在同维度里切换到
    别的选项（只能先改回"全部"），级联筛选会退化成单向不可逆。"""
    filters = {k: (v or "").strip() for k, v in (filters or {}).items()
               if k in FILTER_DIMS and (v or "").strip()}
    base = search_catalog(items, keyword=keyword,
                          include_resources=include_resources, **filters)
    # 同一组"其它条件"会被多个维度复用，缓存避免重复遍历（others 为空时即无筛选全集）
    pool_cache = {}

    def pool_of(others):
        key = tuple(sorted(others.items()))
        if key not in pool_cache:
            pool_cache[key] = search_catalog(
                items, keyword=keyword,
                include_resources=include_resources, **dict(others))
        return pool_cache[key]

    facets = {}
    for dim in FILTER_DIMS:
        others = {k: v for k, v in filters.items() if k != dim}
        # 注意：others 为空时绝不能复用 base——base 已应用了本维度的条件
        counts = {}
        for it in pool_of(others):
            v = (it.get(dim) or "").strip()
            if v:
                counts[v] = counts.get(v, 0) + 1
        order = DIM_ORDERS.get(dim)
        if order:
            keys = [v for v in order if v in counts]
            keys += [v for v in counts if v not in order]
        else:
            keys = sorted(counts, key=lambda v: (-counts[v], v))
        facets[dim] = [{"value": v, "count": counts[v]} for v in keys]
    # 一并返回全条件命中结果，调用方无需再跑一次全量筛选
    return {"total": len(base), "items": base, "facets": facets}


def relax_suggestions(items, filters, keyword=None, include_resources=False,
                      limit=3):
    """空结果时的放宽建议：逐个去掉已选条件，看能找回多少条。

    真人遇到"没有匹配的教材"时最想知道的是"我哪一步选错了"，而不是一句
    干巴巴的提示。返回按找回数量降序的 [{dim, value, count}]。"""
    filters = {k: (v or "").strip() for k, v in (filters or {}).items()
               if k in FILTER_DIMS and (v or "").strip()}
    hints = []
    for dim, val in filters.items():
        rest = {k: v for k, v in filters.items() if k != dim}
        n = len(search_catalog(items, keyword=keyword,
                               include_resources=include_resources, **rest))
        if n:
            hints.append({"dim": dim, "label": DIM_LABELS.get(dim, dim),
                          "value": val, "count": n})
    hints.sort(key=lambda h: -h["count"])
    return hints[:limit]


def parse_selection(text, total):
    """解析 '1,3-5' 形式的编号选择，返回 0 基去重升序索引列表。"""
    picked = set()
    for part in text.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            picked.update(n - 1 for n in range(a, b + 1) if 1 <= n <= total)
        elif part.isdigit():
            n = int(part)
            if 1 <= n <= total:
                picked.add(n - 1)
    return sorted(picked)


def _pad(text, width):
    """按显示宽度补齐/截断（中文按 2 列计），保证表格对齐。"""
    text = text or ""
    width_sum = lambda s: sum(2 if ord(c) > 127 else 1 for c in s)
    if width_sum(text) <= width:
        return text + " " * (width - width_sum(text))
    out, used = "", 0
    for c in text:
        cw = 2 if ord(c) > 127 else 1
        if used + cw > width - 1:
            break
        out += c
        used += cw
    return out + "…"


def print_catalog_matches(matches, limit=200):
    headers = ("No", "学段", "年级", "科目", "版本", "册次", "书名")
    widths = (4, 10, 7, 13, 11, 7, 46)
    print("  ".join(_pad(h, w) for h, w in zip(headers, widths)).rstrip())
    for i, it in enumerate(matches, 1):
        if i > limit:
            print(f"  ...（其余 {len(matches) - limit} 条省略）")
            break
        pub = it.get("publisher") or "-"
        if it.get("pub_variant"):
            pub = f"{pub}（{it['pub_variant']}）"
        row = (str(i), it.get("phase") or "-", it.get("grade") or "-",
               it.get("subject") or "-", pub,
               it.get("volume") or it.get("module") or "-", it.get("title") or "")
        print("  ".join(_pad(v, w) for v, w in zip(row, widths)).rstrip())


def print_facets(items, filters=None, keyword=None):
    """打印级联候选值：便于命令行下逐级收窄条件（--show-facets）。"""
    result = catalog_facets(items, filters, keyword=keyword)
    print(f"\n当前条件下命中 {result['total']} 本，各维度可选值：")
    for dim in FILTER_DIMS:
        opts = result["facets"].get(dim) or []
        if not opts:
            continue
        shown = ", ".join(f"{o['value']}({o['count']})" for o in opts[:12])
        more = f"  …等 {len(opts)} 项" if len(opts) > 12 else ""
        print(f"  {DIM_LABELS.get(dim, dim):<5} {shown}{more}")


def run_catalog_search(args):
    """--search/--phase/--grade/--subject/--publisher 触发的目录搜索模式。"""
    print("正在加载教材目录（首次约 40MB，之后 7 天内走本地缓存）...")
    try:
        items = fetch_catalog_index(timeout=args.timeout,
                                    on_log=lambda m: print(f"  {m}"))
    except (DownloadError, requests.RequestException) as e:
        print(f"目录获取失败: {e}")
        return 1

    filters = {d: getattr(args, d, None) for d in FILTER_DIMS}
    if args.show_facets:
        print_facets(items, filters, keyword=args.search)
        return 0

    matches = search_catalog(items, keyword=args.search,
                             include_resources=args.include_resources, **filters)
    if not matches:
        print("没有匹配的教材。可尝试：放宽筛选条件 / 换个关键词 / 只用 --search。")
        return 1

    print(f"\n共匹配 {len(matches)} 本教材：\n")
    print_catalog_matches(matches)

    if args.list_only:
        return 0
    if args.all:
        picked = matches
    elif sys.stdin and sys.stdin.isatty():
        try:
            raw = input("输入要下载的编号（如 1,3-5；a=全部；回车取消）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not raw:
            print("已取消。")
            return 0
        if raw.lower() in ("a", "all", "全部"):
            picked = matches
        else:
            idx = parse_selection(raw, len(matches))
            if not idx:
                print("未选中任何有效编号。")
                return 1
            picked = [matches[i] for i in idx]
    else:
        print("\n（非交互环境仅列出结果；加 --all 可直接下载全部匹配项）")
        return 0

    ordered = [detail_page_url(m["id"]) for m in picked]
    print(f"\n已选择 {len(ordered)} 本，开始下载...")
    return run_downloads(ordered, args)


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
        (on_log or print)(msg)

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


# ---------- 汇总输出 ----------
def print_summary(results):
    ok = [r for r in results if r["status"] == "ok"]
    skip = [r for r in results if r["status"] == "skip"]
    fail = [r for r in results if r["status"] == "fail"]
    print("\n" + "=" * 52)
    print(f"下载汇总: 成功 {len(ok)} | 跳过 {len(skip)} | 失败 {len(fail)} | 共 {len(results)}")
    if fail:
        print("-" * 52)
        print("失败明细:")
        for r in fail:
            name = r["title"] or (r["entry"] if len(r["entry"]) <= 40 else r["entry"][:37] + "...")
            print(f"  x {name}: {r['msg']}")
    print("=" * 52)


# ---------- 命令行入口 ----------
def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="smartedu_downloader.py",
        description="书到了（ShudaoLe）· 国家中小学智慧教育平台教材 PDF 下载工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            '  python smartedu_downloader.py "https://basic.smartedu.cn/tchMaterial/detail?...&contentId=xxxx"\n'
            "  python smartedu_downloader.py <链接或ID1> <链接或ID2> -o \"D:/教材\"\n"
            "  python smartedu_downloader.py -f links.txt -o ./downloads\n"
            "  python smartedu_downloader.py            # 交互模式，粘贴多行链接后空行结束\n"
            "\n"
            "目录搜索（按学段/年级/科目/版本找教材，无需粘贴链接）:\n"
            "  python smartedu_downloader.py --search 语文 --phase 小学 --grade 一年级\n"
            "  python smartedu_downloader.py --subject 数学 --grade 初一 --publisher 人教\n"
            "  python smartedu_downloader.py --phase 高中 --subject 英语 --list-only\n"
            "  python smartedu_downloader.py --phase 小学 --grade 一年级 --subject 语文 --all\n"
            "\n"
            "登录令牌: 平台对 PDF 下载启用了鉴权，需提供浏览器登录后的 x-nd-auth 值\n"
            "(--token 参数 / SMARTEDU_TOKEN 环境变量 / token.txt 文件 / 提示时交互粘贴)。"
        ),
    )
    parser.add_argument("inputs", nargs="*",
                        help="教材详情页链接或 contentId，可同时传多个")
    parser.add_argument("-f", "--file",
                        help="批量文件，每行一个链接或 contentId（# 开头为注释）")
    parser.add_argument("--search", metavar="关键词",
                        help="目录搜索模式：按书名关键词查找（可与下面筛选组合）")
    parser.add_argument("--phase", metavar="学段",
                        help="筛选学段（小学/初中/高中）")
    parser.add_argument("--grade", metavar="年级",
                        help="筛选年级（一年级/初一/高一 等）")
    parser.add_argument("--subject", metavar="科目",
                        help="筛选科目（语文/数学/英语 等）")
    parser.add_argument("--publisher", metavar="版本",
                        help="筛选版本：分组名（人教版系，含 人教版/统编版/人教鄂教版）"
                             "、真实标签（统编版、人教版）或简写（人教）均可")
    parser.add_argument("--volume", metavar="册次",
                        help="筛选册次（上册/下册/全一册 等）")
    parser.add_argument("--audience", metavar="用途",
                        help="筛选用途（教师用书/学生用书 等）")
    parser.add_argument("--system", metavar="学制",
                        help="筛选学制（六三学制/五四学制 等）")
    parser.add_argument("--school", metavar="学校",
                        help="筛选学校类型（普通学校/盲校/聋校/培智学校 等）")
    parser.add_argument("--editor", metavar="主编",
                        help="筛选主编/作者（如 主编:吴欣 或直接 吴欣）")
    parser.add_argument("--module", metavar="模块",
                        help="筛选教材模块（如 选择性必修）")
    parser.add_argument("--all", action="store_true",
                        help="下载搜索命中的全部教材（默认交互选择编号）")
    parser.add_argument("--list-only", action="store_true",
                        help="仅列出搜索结果，不进入下载")
    parser.add_argument("--show-facets", action="store_true",
                        help="级联查看：打印当前条件下各维度可选值与命中数")
    parser.add_argument("--include-resources", action="store_true",
                        help="搜索时也包含课件/课时等非教材资源（默认仅教材）")
    parser.add_argument("-o", "--output", default="downloads",
                        help="输出目录（默认: ./downloads）")
    parser.add_argument("--token", help="X-ND-AUTH 登录令牌")
    parser.add_argument("--retries", type=int, default=3,
                        help="每个下载地址的重试次数（默认: 3）")
    parser.add_argument("--timeout", type=int, default=30,
                        help="单次请求超时秒数（默认: 30）")
    parser.add_argument("--no-save-token", action="store_true",
                        help="不将交互输入的令牌保存到 token.txt")
    parser.add_argument("--flat-name", action="store_true",
                        help="用纯书名命名（默认用「学段科目_版本_年级册次」结构化命名）")
    return parser


def run_downloads(ordered, args):
    """下载队列主循环（链接模式与目录搜索模式共用），返回退出码。"""
    out_dir = Path(args.output).expanduser()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"创建输出目录失败: {e}")
        return 1

    auth = AuthContext(initial_token(args.token), save_token=not args.no_save_token)
    print(f"输出目录: {out_dir.resolve()}")
    if auth.token:
        print("已加载登录令牌（来自 命令行/环境变量/token.txt）")
    print(f"共 {len(ordered)} 条待处理")

    results = []
    try:
        for i, entry in enumerate(ordered, 1):
            preview = entry if len(entry) <= 72 else entry[:69] + "..."
            print(f"\n[{i}/{len(ordered)}] {preview}")
            try:
                results.append(process_one(entry, out_dir, auth,
                                           args.retries, args.timeout,
                                           flat_name=args.flat_name))
            except KeyboardInterrupt:
                raise
            except Exception as e:  # 兜底：任何未预料异常不中断批量任务
                results.append(_result(entry, None, "fail",
                                       f"未预料异常: {type(e).__name__}: {e}"))
            status = results[-1]
            if status["status"] == "ok":
                print(f"  下载完成: {status['msg']}")
            elif status["status"] == "skip":
                print(f"  {status['msg']}")
            else:
                print(f"  失败: {status['msg']}")
    except KeyboardInterrupt:
        print("\n\n用户中断，正在退出...")

    print_summary(results)
    return 1 if any(r["status"] == "fail" for r in results) else 0


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # 目录搜索模式：给了任一搜索/筛选参数即触发
    if any([args.search, args.phase, args.grade, args.subject, args.publisher]):
        return run_catalog_search(args)

    # 收集输入：位置参数 + 文件；都为空且是终端 -> 交互模式
    entries = list(args.inputs)
    if args.file:
        try:
            entries.extend(load_links_from_file(args.file))
        except OSError as e:
            print(f"读取批量文件失败: {e}")
            return 1
    if not entries:
        if sys.stdin and sys.stdin.isatty():
            entries = interactive_input()
        else:
            parser.print_help()
            return 1

    # 去重并保持顺序
    ordered = list(dict.fromkeys(e.strip() for e in entries if e.strip()))
    if not ordered:
        print("未提供任何有效链接。")
        return 1

    return run_downloads(ordered, args)


if __name__ == "__main__":
    sys.exit(main())

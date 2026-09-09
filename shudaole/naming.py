# -*- coding: utf-8 -*-
"""链接解析、元数据提取与结构化命名（纯函数，便于测试）。"""

import re

from .config import path_is_relative_to
from .errors import DownloadError
CONTENT_ID_RE = re.compile(r"contentId=([0-9a-zA-Z\-_]{8,})")

CONTENT_TYPE_RE = re.compile(r"contentType=([\w-]+)")

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|\r\n\t]+')



# ---------- 输入解析 ----------
def parse_content_id(text):
    """从详情页链接或裸 contentId 中提取 contentId；失败返回 None"""
    text = (text or "").strip()
    if UUID_RE.match(text):
        return text.lower()
    m = CONTENT_ID_RE.search(text)
    return m.group(1) if m else None



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



# Windows 保留设备名（CON/NUL/COM1...）：不带扩展名也占用，作文件名必失败。
# 判定对象是首个点之前的主名（"CON.pdf" 同样保留）。
_RESERVED_DEVICE_RE = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE)


# ---------- 文件名与本地路径 ----------
def sanitize_filename(name):
    """清理 Windows 非法字符、首尾空格与点，限制长度"""
    name = ILLEGAL_CHARS_RE.sub("_", (name or "").strip()).strip(" .")
    name = name[:120] or "未命名教材"
    if _RESERVED_DEVICE_RE.match(name.split(".", 1)[0]):
        name = "_" + name
    return name



def resolve_dest(out_dir, base_name):
    """确定目标文件路径。返回 (路径, 是否跳过)；
    已存在同名且体积 >1MB 的文件视为已下载完成，跳过。
    base_name 由远端书名生成，这里显式校验最终路径不会越出 out_dir。"""
    out_root = out_dir.resolve()
    candidate = out_dir / f"{base_name}.pdf"
    i = 1
    while True:
        if not path_is_relative_to(candidate, out_root):
            raise DownloadError(f"保存路径越界，拒绝写入: {candidate}")
        if not candidate.exists():
            return candidate, False
        if candidate.stat().st_size > 1_000_000:
            return candidate, True
        i += 1
        candidate = out_dir / f"{base_name}({i}).pdf"



TAG_DIM_MAP = {
    "zxxxd": "phase",       # 学段（小学/初中/高中）
    "zxxnj": "grade",       # 年级
    "zxxxk": "subject",     # 科目
    "zxxbb": "publisher",   # 版本（出版社）
    "zxxcc": "semester",    # 册别（上册/下册/全一册）
}


# 缓存结构版本：规范化规则变更时递增，旧缓存会被就地重算而非重新下载

# ---------- 标题规范化用的正则 battery（catalog.normalize_catalog 也复用） ----------

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

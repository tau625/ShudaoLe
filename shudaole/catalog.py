# -*- coding: utf-8 -*-
"""教材目录：抓取/缓存、标题规范化、级联筛选与搜索。"""

import json
import os
import re
import time

import requests

from .errors import DownloadError
from .net import validate_public_http_url, UA
from .config import CATALOG_CACHE
from .logutil import get_logger
from .naming import (_normalize_grade_meta, normalize_title, TAG_DIM_MAP,
                     _split_parens, _EDITOR_RE, _GRADE_IN_TITLE_RE,
                     _SPECIAL_SCHOOL_RE, _RESOURCE_RE,
                     _VOLUME_RE)

# ---------- 教材目录（按 学段/年级/科目/版本 自动检索） ----------
CATALOG_PARTS = (100, 101, 102, 103)  # 平台教材目录分片（实测 104 起已下线）

CATALOG_URL = "https://s-file-{n}.ykt.cbern.com.cn/zxx/ndrs/resources/tch_material/part_{p}.json"

CATALOG_TTL = 7 * 86400  # 目录缓存有效期（秒）
# 平台标签维度 -> 本工具字段名

CATALOG_SCHEMA = 5



def fetch_catalog_index(force=False, timeout=60, on_log=None):
    """获取全部教材目录索引，返回规范化后的列表，每项形如:
        {"id", "title", "phase", "system", "grade", "subject", "publisher",
         "editor", "pub_variant", "volume", "module", "audience", "school",
         "kind", 以及 *_raw 原始标签值}
    优先使用本地缓存 catalog_cache.json（7 天有效）；force=True 强制重新下载。
    注意: 首次下载约 40MB（4 个分片），耗时取决于网速。"""
    log = on_log or (lambda m: get_logger().info(m))
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
        for row in data:
            cid = row.get("id")
            title = (row.get("title") or "").strip()
            if not cid or cid in seen or not title:
                continue
            seen.add(cid)
            entry = {"id": cid, "title": title}
            for t in row.get("tag_list") or []:
                dim = TAG_DIM_MAP.get(t.get("tag_dimension_id"))
                if dim and not entry.get(dim):
                    entry[dim] = (t.get("tag_name") or "").strip()
            items.append(entry)
        log(f"目录分片 part_{p} 完成，累计 {len(items)} 条教材")

    items = normalize_catalog(items)
    _write_catalog_cache(items, log)
    return items



def _write_catalog_cache(items, log):
    """把目录写入磁盘缓存（原子写：先写 .tmp 再 os.replace）。

    旧实现直接 write_text，进程被中断会留下半截 JSON（下次读取必失败，只能重下）；
    且失败静默吞掉。改为临时文件 + 原子替换，任何时刻磁盘上都只有完整文件，
    失败则给出原因（缓存只是加速手段，失败不影响使用）。
    """
    tmp = CATALOG_CACHE.parent / (CATALOG_CACHE.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps({"fetched_at": time.time(), "schema": CATALOG_SCHEMA,
                        "items": items}, ensure_ascii=False),
            encoding="utf-8")
        os.replace(str(tmp), str(CATALOG_CACHE))
        log(f"目录已缓存到 {CATALOG_CACHE.name}（7 天内无需重新下载）")
    except OSError as e:
        log(f"目录缓存写入失败（不影响使用，下次仍会重新拉取）: {e}")
        try:
            tmp.unlink(missing_ok=True)
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


# ---------- 标题清洗 ----------
# 平台标题混用「间隔号 ·」「项目符 •」且带大量冗余前缀，直接展示/命名会格式不一：
#   （根据2022年版课程标准修订）义务教育教科书•艺术•音乐（简谱）八年级下册
#   → 艺术·音乐（简谱）八年级下册
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



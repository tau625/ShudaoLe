# -*- coding: utf-8 -*-
"""目录模块测试：规范化、级联筛选、分面、缓存原子写。"""
import json


from shudaole import catalog
from shudaole.catalog import (
    DEFAULT_FILTERS, dim_match, expand_publisher_filter, normalize_catalog,
    publisher_facets, publisher_label, search_catalog, _write_catalog_cache,
    relax_suggestions, PUB_GROUPS, SUPPORTED_SCOPE,
)


# ---------- dim_match ----------

def test_dim_match_empty_query_always_true(sample_items):
    for it in sample_items:
        assert dim_match(it, "phase", "") is True


def test_dim_match_substring(sample_items):
    assert dim_match(sample_items[0], "subject", "语") is True
    assert dim_match(sample_items[0], "subject", "数学") is False
    # 反向包含不算命中：查"语文学科"不应命中"语文"
    assert dim_match(sample_items[0], "subject", "语文学科") is False


def test_dim_match_grade_alias(sample_items):
    # "初一" 是 "七年级" 的口语别名
    assert dim_match(sample_items[1], "grade", "初一") is True


# ---------- search_catalog ----------

def test_search_by_keyword(sample_items):
    hits = search_catalog(sample_items, keyword="语文")
    assert [it["id"] for it in hits] == ["id-1"]


def test_search_excludes_resources_by_default(sample_items):
    hits = search_catalog(sample_items, keyword="英语")
    assert hits == []
    hits = search_catalog(sample_items, keyword="英语", include_resources=True)
    assert [it["id"] for it in hits] == ["id-3"]


def test_search_with_filters(sample_items):
    hits = search_catalog(sample_items, phase="小学")
    assert [it["id"] for it in hits] == ["id-1"]


# ---------- publisher 处理 ----------

def test_expand_publisher_group():
    group = next(iter(PUB_GROUPS))
    tags = expand_publisher_filter(group)
    assert tags  # 组名展开为组内标签


def test_publisher_facets_sample(sample_items):
    facets = publisher_facets([{"value": "统编版", "count": 1},
                               {"value": "人教版", "count": 2}])
    assert isinstance(facets, list)


# ---------- 常量一致性 ----------

def test_default_filters_within_scope():
    # audience 的「学生用书」是排除语义（AUDIENCE_EXCLUDE），不属 SUPPORTED_SCOPE
    for dim, val in DEFAULT_FILTERS.items():
        if dim == "audience":
            continue
        assert val in SUPPORTED_SCOPE[dim], dim


def test_default_audience_is_student():
    """首屏默认只看学生用书（排除教师用书），家长不易误下教师版。"""
    assert DEFAULT_FILTERS.get("audience") == "学生用书"


def test_audience_exclude_semantics(sample_items):
    """「学生用书」= 排除教师用书；未标记 audience 的条目默认学生用书（命中）。"""
    for it in sample_items:
        assert dim_match(it, "audience", "学生用书") is True
    teacher = dict(sample_items[0], audience="教师用书")
    assert dim_match(teacher, "audience", "学生用书") is False
    assert dim_match(teacher, "audience", "教师用书") is True


def test_all_phases_open():
    """学段全开放：中小学各学段（含特殊教育）均在支持范围内。"""
    assert set(SUPPORTED_SCOPE["phase"]) >= {"小学", "初中", "高中", "特殊教育"}


# ---------- publisher 全量可见 ----------

def test_publisher_label_ungrouped_visible():
    """SHOW_ALL_PUBLISHERS 开启：未分组标签顶层可见（返回自身）。"""
    assert publisher_label("教科版") == "教科版"   # 未收入任何分组的标签
    grouped = next(iter(PUB_GROUPS))
    first_tag = PUB_GROUPS[grouped][0]
    assert publisher_label(first_tag) == grouped
    assert publisher_label("北师大版") == "北师大版系"   # 已收入分组的返回分组名


def test_publisher_facets_shows_ungrouped():
    """未分组标签按结果数降序补到顶层，且不带分组信息。"""
    facets = publisher_facets([{"value": "北师大版", "count": 7},
                               {"value": "统编版", "count": 1},
                               {"value": "人教版", "count": 2},
                               {"value": "外研版", "count": 4}])
    values = [f["value"] for f in facets]
    assert "北师大版" in values and "外研版" in values
    rest = [f for f in facets if f["value"] in ("北师大版", "外研版")]
    assert all(f["group"] is None and not f["is_group"] for f in rest)
    counts = {f["value"]: f["count"] for f in rest}
    assert counts["北师大版"] > counts["外研版"]  # 降序


def test_shutdown_browser_dead_proc_noop():
    """进程已退出时 _shutdown_browser 应立即返回（不依赖真实浏览器）。"""
    from shudaole import token as token_mod

    class _DeadProc:
        def poll(self):
            return 0

    token_mod._shutdown_browser(1, _DeadProc())  # 端口 1 不可达 → 走兜底 → 进程已死直接返回


# ---------- 筛选防错：版本提示 / 快捷组合 / 多版本风险 ----------

def test_publisher_meta_known_and_unknown():
    from shudaole.catalog import publisher_meta
    assert "人民教育出版社" in publisher_meta("人教版")
    assert publisher_meta("人教版系")          # 分组名回退到组内成员文案
    assert publisher_meta("不存在的版本") == ""


def test_quick_combos_generated(sample_items):
    from shudaole.catalog import quick_combos
    combos = quick_combos(sample_items)
    labels = [c["label"] for c in combos]
    # sample 里有 小学+人教版（属人教版系组），应生成「小学·人教全套」
    assert any(c["phase"] == "小学" and c["publisher"] == "人教版系" for c in combos), labels
    assert all(c["phase"] != "特殊教育" for c in combos)


def test_multi_version_risk():
    from shudaole.catalog import multi_version_risk
    items = [
        {"kind": "textbook", "grade": "七年级", "subject": "数学", "publisher": "人教版"},
        {"kind": "textbook", "grade": "七年级", "subject": "数学", "publisher": "北师大版"},
        {"kind": "textbook", "grade": "七年级", "subject": "语文", "publisher": "统编版"},
    ]
    risk, count, versions = multi_version_risk(items, "七年级", "数学")
    assert risk is True and count == 2 and set(versions) == {"人教版", "北师大版"}
    risk2, count2, _ = multi_version_risk(items, "七年级", "语文")
    assert risk2 is False and count2 == 1


# ---------- normalize_catalog ----------

def test_normalize_catalog_basic():
    raw = [{
        "id": "x1",
        "title": "（根据2024年版课程标准修订）数学 七年级 下册",
        "resource_type_code": "tch_material",
        "tag_list": [
            {"tag_dimension_id": "zxxxd", "tag_name": "初中"},
            {"tag_dimension_id": "zxxnj", "tag_name": "七年级"},
            {"tag_dimension_id": "zxxxk", "tag_name": "数学"},
            {"tag_dimension_id": "zxxbb", "tag_name": "人教版"},
            {"tag_dimension_id": "zxxcc", "tag_name": "下册"},
        ],
    }]
    items = normalize_catalog(raw)
    assert items and items[0]["title"] == "数学七年级下册"
    assert items[0]["phase"] == "初中"
    assert items[0]["grade"] == "七年级"
    assert items[0]["subject"] == "数学"
    assert items[0]["volume"] == "下册"
    assert items[0]["kind"] == "textbook"
    assert items[0]["title_raw"].startswith("（根据")  # 原始标题保留


# ---------- 缓存原子写 ----------

def test_write_catalog_cache_atomic(tmp_path, monkeypatch):
    cache = tmp_path / "catalog_cache.json"
    monkeypatch.setattr(catalog, "CATALOG_CACHE", cache)
    logs = []
    _write_catalog_cache([{"title": "测试教材"}], logs.append)
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["items"] == [{"title": "测试教材"}]
    assert not list(tmp_path.glob("*.tmp"))  # 原子替换后无残留


def test_write_catalog_cache_failure_tolerant(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "CATALOG_CACHE",
                        tmp_path / "no-such-dir" / "c.json")
    logs = []
    _write_catalog_cache([{"x": 1}], logs.append)  # 不应抛异常
    assert logs  # 失败有日志说明


# ---------- relax_suggestions ----------

def test_relax_suggestions_returns_list(sample_items):
    out = relax_suggestions(sample_items, {"phase": "高中", "subject": "语文"},
                            keyword=None)
    assert isinstance(out, list)

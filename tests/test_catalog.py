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
    for dim, val in DEFAULT_FILTERS.items():
        assert val in SUPPORTED_SCOPE[dim], dim


def test_all_phases_open():
    """学段全开放：中小学各学段（含特殊教育）均在支持范围内。"""
    assert set(SUPPORTED_SCOPE["phase"]) >= {"小学", "初中", "高中", "特殊教育"}


# ---------- publisher 全量可见 ----------

def test_publisher_label_ungrouped_visible():
    """SHOW_ALL_PUBLISHERS 开启：未分组标签顶层可见（返回自身）。"""
    assert publisher_label("北师大版") == "北师大版"
    grouped = next(iter(PUB_GROUPS))
    first_tag = PUB_GROUPS[grouped][0]
    assert publisher_label(first_tag) == grouped


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

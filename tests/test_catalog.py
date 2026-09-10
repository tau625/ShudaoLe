# -*- coding: utf-8 -*-
"""目录模块测试：规范化、级联筛选、分面、缓存原子写、抓取自愈。"""
import json
import re
import time

import pytest
import requests_mock as rm_module

from shudaole import catalog
from shudaole.catalog import (
    DEFAULT_FILTERS, dim_match, expand_publisher_filter, normalize_catalog,
    publisher_facets, publisher_label, search_catalog, _write_catalog_cache,
    relax_suggestions, PUB_GROUPS, SUPPORTED_SCOPE,
)


@pytest.fixture
def requests_mock():
    with rm_module.Mocker() as m:
        yield m


# ---------- fetch_catalog_index：分片抓取失败面（2026-09-10 回归锁） ----------

_PARTS_RE = re.compile(
    r"https://s-file-[123]\.ykt\.cbern\.com\.cn/zxx/ndrs/resources"
    r"/tch_material/part_10[0-3]\.json")

_ROWS = [
    {"id": "cid-1", "title": "数学 七年级 下册",
     "tag_list": [{"tag_dimension_id": "zxxxd", "tag_name": "初中"},
                  {"tag_dimension_id": "zxxnj", "tag_name": "七年级"},
                  {"tag_dimension_id": "zxxxk", "tag_name": "数学"},
                  {"tag_dimension_id": "zxxbb", "tag_name": "人教版"}]},
    {"id": "cid-2", "title": "语文 一年级 上册",
     "tag_list": [{"tag_dimension_id": "zxxxd", "tag_name": "小学"},
                  {"tag_dimension_id": "zxxnj", "tag_name": "一年级"},
                  {"tag_dimension_id": "zxxxk", "tag_name": "语文"}]},
]


def _mock_parts(requests_mock, **kwargs):
    requests_mock.get(_PARTS_RE, **kwargs)


def test_fetch_catalog_non_json_response(requests_mock, tmp_path, monkeypatch):
    """200 + HTML 错误页：报「非 JSON」而不是裸 JSONDecodeError traceback。"""
    monkeypatch.setattr(catalog, "CATALOG_CACHE", tmp_path / "cache.json")
    monkeypatch.setattr(catalog, "validate_public_http_url", lambda url: url)
    _mock_parts(requests_mock, text="<html>platform error page</html>")
    from shudaole.errors import DownloadError
    with pytest.raises(DownloadError) as ei:
        catalog.fetch_catalog_index(force=True)
    assert "非 JSON" in str(ei.value)


def test_fetch_catalog_non_list_payload(requests_mock, tmp_path, monkeypatch):
    """200 + dict（接口改版）：报「格式异常」而不是 AttributeError。"""
    monkeypatch.setattr(catalog, "CATALOG_CACHE", tmp_path / "cache.json")
    monkeypatch.setattr(catalog, "validate_public_http_url", lambda url: url)
    _mock_parts(requests_mock, json={"code": 1, "message": "error"})
    from shudaole.errors import DownloadError
    with pytest.raises(DownloadError) as ei:
        catalog.fetch_catalog_index(force=True)
    assert "格式异常" in str(ei.value)


def test_fetch_catalog_skips_non_dict_rows(requests_mock, tmp_path, monkeypatch):
    """分片里混入非 dict 行：跳过该行，整片其余条目照常入库。"""
    monkeypatch.setattr(catalog, "CATALOG_CACHE", tmp_path / "cache.json")
    monkeypatch.setattr(catalog, "validate_public_http_url", lambda url: url)
    _mock_parts(requests_mock, json=[_ROWS[0], "garbage-row", None, _ROWS[1]])
    items = catalog.fetch_catalog_index(force=True)
    assert [it["id"] for it in items] == ["cid-1", "cid-2"]


def test_fetch_catalog_dns_failure_rotates_host(requests_mock, tmp_path, monkeypatch):
    """s-file-1 域名解析失败（DownloadError）：轮换 s-file-2/3 而非整次失败。"""
    monkeypatch.setattr(catalog, "CATALOG_CACHE", tmp_path / "cache.json")
    from shudaole.errors import DownloadError as _DE

    def flaky_validate(url):
        if "s-file-1" in url:
            raise _DE("域名解析失败: s-file-1.ykt.cbern.com.cn")
        return url
    monkeypatch.setattr(catalog, "validate_public_http_url", flaky_validate)
    for n in (2, 3):
        for p in (100, 101, 102, 103):
            requests_mock.get(
                f"https://s-file-{n}.ykt.cbern.com.cn/zxx/ndrs/resources"
                f"/tch_material/part_{p}.json", json=_ROWS)
    items = catalog.fetch_catalog_index(force=True)
    assert len(items) == 2


def test_fetch_catalog_cache_garbage_json_self_heals(requests_mock, tmp_path,
                                                     monkeypatch):
    """缓存文件是垃圾（非法 JSON）：静默忽略并走网络重新抓取。"""
    cache = tmp_path / "catalog_cache.json"
    cache.write_text("{不是合法的 JSON!!!", encoding="utf-8")
    monkeypatch.setattr(catalog, "CATALOG_CACHE", cache)
    monkeypatch.setattr(catalog, "validate_public_http_url", lambda url: url)
    _mock_parts(requests_mock, json=_ROWS)
    items = catalog.fetch_catalog_index(force=False)
    assert [it["id"] for it in items] == ["cid-1", "cid-2"]
    # 自愈成功后新缓存已写回（合法 JSON）
    assert json.loads(cache.read_text(encoding="utf-8"))["items"]


def test_fetch_catalog_cache_broken_structure_self_heals(requests_mock, tmp_path,
                                                         monkeypatch):
    """缓存是合法 JSON 但结构损坏（items 非列表）：
    记日志「本地缓存损坏」并走网络分支，不再裸 traceback。"""
    cache = tmp_path / "catalog_cache.json"
    cache.write_text(json.dumps({"items": "一坨损坏的数据",
                                 "fetched_at": time.time(),
                                 "schema": catalog.CATALOG_SCHEMA}),
                     encoding="utf-8")
    monkeypatch.setattr(catalog, "CATALOG_CACHE", cache)
    monkeypatch.setattr(catalog, "validate_public_http_url", lambda url: url)
    _mock_parts(requests_mock, json=_ROWS)
    logs = []
    items = catalog.fetch_catalog_index(force=False, on_log=logs.append)
    assert [it["id"] for it in items] == ["cid-1", "cid-2"]
    assert any("本地缓存损坏" in m for m in logs)


def test_fetch_catalog_tiny_result_warns(requests_mock, tmp_path, monkeypatch):
    """抓取结果远小于正常规模（~4 万条）时给「分片可能已变更」警告。"""
    monkeypatch.setattr(catalog, "CATALOG_CACHE", tmp_path / "cache.json")
    monkeypatch.setattr(catalog, "validate_public_http_url", lambda url: url)
    _mock_parts(requests_mock, json=_ROWS)
    logs = []
    catalog.fetch_catalog_index(force=True, on_log=logs.append)
    assert any("异常偏少" in m for m in logs)


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


# ---------- system / phase 视图语义（网页「五四学制」「特殊教育」开关） ----------

def _it(system="", phase="小学", id="x", school=""):
    return {"id": id, "kind": "textbook", "title": "数学", "title_raw": "数学",
            "phase": phase, "grade": "一年级", "subject": "数学",
            "publisher": "人教版", "volume": "上册", "system": system,
            "school": school}


def test_system_fallback_unmarked_is_regular():
    """平台只显式标注五四学制；未标注一律视为常规（六·三）。"""
    unmarked = _it(system="")
    assert dim_match(unmarked, "system", "六·三学制") is True
    assert dim_match(unmarked, "system", "五·四学制") is False
    regular = _it(system="六·三学制")
    assert dim_match(regular, "system", "六·三学制") is True
    assert dim_match(regular, "system", "五·四学制") is False


def test_system_explicit_54():
    wusi = _it(system="五·四学制")
    assert dim_match(wusi, "system", "五·四学制") is True
    assert dim_match(wusi, "system", "六·三学制") is False


def test_system_aliases():
    """口语写法（五四学制/六三学制）经别名归一后同样命中。"""
    wusi = _it(system="五·四学制")
    assert dim_match(wusi, "system", "五四学制") is True
    regular = _it(system="")
    assert dim_match(regular, "system", "六三学制") is True


def test_school_sentinels():
    """school 哨兵：「特殊学校」= 盲校/聋校/培智任一；「常规学校」= school 为空。

    注意盲校/聋校的 phase 是小学/初中，只有培智 phase=特殊教育——
    特殊学校教材必须按 school 维度拎出来，按 phase 排不干净。"""
    blind = _it(school="盲校", phase="小学")
    deaf = _it(school="聋校", phase="初中")
    peizhi = _it(school="培智", phase="特殊教育")
    normal = _it(school="")
    for it in (blind, deaf, peizhi):
        assert dim_match(it, "school", "特殊学校") is True
        assert dim_match(it, "school", "常规学校") is False
    assert dim_match(normal, "school", "常规学校") is True
    assert dim_match(normal, "school", "特殊学校") is False


def test_school_alias_common_school():
    """「普通学校」是口语直觉写法，经 DIM_ALIASES 映射到哨兵值「常规学校」，
    不再走子串匹配而永远命中 0 条。"""
    blind = _it(school="盲校")
    normal = _it(school="")
    assert dim_match(normal, "school", "普通学校") is True
    assert dim_match(blind, "school", "普通学校") is False
    # 端到端：search_catalog 全链路同样生效
    items = [normal, blind]
    assert [it["school"] for it in search_catalog(items, school="普通学校")] == [""]


def test_system_views_partition_catalog(sample_items):
    """五四视图 + 常规视图 = 全集（无重叠、无遗漏）。"""
    items = sample_items + [_it(system="五·四学制", id="id-54"),
                            _it(system="", phase="特殊教育", id="id-spec")]
    full = search_catalog(items)
    wusi = search_catalog(items, system="五·四学制")
    regular = search_catalog(items, system="六·三学制")
    assert {it["id"] for it in full} == ({it["id"] for it in wusi}
                                         | {it["id"] for it in regular})
    assert not ({it["id"] for it in wusi} & {it["id"] for it in regular})


def test_regular_view_excludes_special(sample_items):
    """常规视图（六·三 + 常规学校）同时排除五四变体与全部特殊学校教材。"""
    items = sample_items + [_it(system="五·四学制", id="id-54"),
                            _it(school="盲校", phase="小学", id="id-blind"),
                            _it(school="培智", phase="特殊教育", id="id-spec")]
    ids = {it["id"] for it in search_catalog(items, system="六·三学制",
                                             school="常规学校")}
    assert ids == {"id-1", "id-2"}


def test_special_school_view(sample_items):
    """特教视图（school=特殊学校）显示全部盲校/聋校/培智教材。"""
    items = sample_items + [_it(school="盲校", phase="小学", id="id-blind"),
                            _it(school="聋校", phase="初中", id="id-deaf"),
                            _it(school="培智", phase="特殊教育", id="id-spec")]
    ids = {it["id"] for it in search_catalog(items, system="六·三学制",
                                             school="特殊学校")}
    assert ids == {"id-blind", "id-deaf", "id-spec"}


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


def test_publisher_facets_global_sort_by_count():
    """全局按命中数降序：组头 count = 成员之和，最常用的排最上。"""
    facets = publisher_facets([{"value": "统编版", "count": 3},
                               {"value": "人教版", "count": 5},
                               {"value": "人教鄂教版", "count": 2},
                               {"value": "北师大版", "count": 7},
                               {"value": "华东师大版", "count": 1}])
    counts = [f["count"] for f in facets]
    assert counts == sorted(counts, reverse=True), facets
    group_vals = {f["value"]: f["count"] for f in facets if f["is_group"]}
    # 人教版系 count = 3 + 5 + 2 = 10
    assert group_vals.get("人教版系") == 10
    # 人教版系 (10) > 北师大版系 (8) > 北师大版 (7) > 人教版 (5) > ...
    top_values = [f["value"] for f in facets]
    assert top_values.index("人教版系") < top_values.index("北师大版系")


def test_publisher_facets_no_optgroup_in_output():
    """输出是平铺数组，不带 group 字段供前端做分层渲染——后端只提供计数排序。"""
    facets = publisher_facets([{"value": "统编版", "count": 3},
                               {"value": "人教版", "count": 5}])
    # 组员仍带 group 标识（供前端识别"整套"后缀），但这是数据字段不是渲染指令
    assert all("value" in f and "count" in f for f in facets)


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

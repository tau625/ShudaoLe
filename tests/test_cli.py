# -*- coding: utf-8 -*-
"""CLI 测试：编号选择解析、目录搜索模式触发条件。"""
import pytest

from shudaole.catalog import FILTER_DIMS
from shudaole.cli import build_arg_parser, parse_selection, wants_catalog_search


def test_parse_single():
    assert parse_selection("3", 10) == [2]


def test_parse_range_and_mix():
    assert parse_selection("1,3-5", 10) == [0, 2, 3, 4]
    assert parse_selection("1，3-5", 10) == [0, 2, 3, 4]  # 全角逗号兼容


def test_parse_out_of_range_ignored():
    assert parse_selection("99", 3) == []


def test_parse_empty():
    assert parse_selection("", 5) == []
    assert parse_selection("  ", 5) == []


# ---------- 目录搜索模式触发条件（2026-09-10 回归锁） ----------

def test_audience_alone_triggers_search_mode():
    """回归：`--audience 教师用书` 单独使用曾漏判，静默掉进交互式粘贴分支。"""
    args = build_arg_parser().parse_args(["--audience", "教师用书"])
    assert wants_catalog_search(args) is True


@pytest.mark.parametrize("dim", FILTER_DIMS)
def test_every_filter_dim_triggers_search_mode(dim):
    """FILTER_DIMS 全量维度单独使用都必须触发搜索模式（新增维度自动覆盖）。"""
    args = build_arg_parser().parse_args([f"--{dim}", "某值"])
    assert wants_catalog_search(args) is True


def test_search_flag_triggers_search_mode():
    args = build_arg_parser().parse_args(["--search", "语文"])
    assert wants_catalog_search(args) is True


def test_plain_link_input_does_not_trigger_search_mode():
    args = build_arg_parser().parse_args(
        ["https://basic.smartedu.cn/tchMaterial/detail?contentId=abc"])
    assert wants_catalog_search(args) is False


def test_main_with_audience_enters_search_not_interactive(monkeypatch):
    """端到端：main(["--audience", ...]) 必须进入 run_catalog_search，
    而不是（非 tty 下的）打印帮助退出。"""
    import shudaole.cli as cli
    called = {}

    def fake_search(args):
        called["audience"] = args.audience
        return 0

    monkeypatch.setattr(cli, "run_catalog_search", fake_search)
    rc = cli.main(["--audience", "教师用书"])
    assert rc == 0
    assert called == {"audience": "教师用书"}


def test_school_help_mentions_regular_school():
    """--school 的 help 必须写真实哨兵值「常规学校/特殊学校」，
    不能再写永远匹配 0 条的「普通学校」。"""
    for action in build_arg_parser()._actions:
        if "--school" in action.option_strings:
            assert "常规学校" in (action.help or "")
            assert "特殊学校" in (action.help or "")
            assert "普通学校" not in (action.help or "")
            break
    else:
        pytest.fail("--school 参数不存在")

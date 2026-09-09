# -*- coding: utf-8 -*-
"""CLI 辅助测试：编号选择解析。"""
from shudaole.cli import parse_selection


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

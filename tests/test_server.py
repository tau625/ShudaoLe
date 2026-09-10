# -*- coding: utf-8 -*-
"""GUI 服务端纯函数测试：结果条数钳制。"""
from shudaole.gui.server import clamp_result_limit


def test_clamp_limit_normal_values_pass_through():
    assert clamp_result_limit("50") == 50
    assert clamp_result_limit("1") == 1
    assert clamp_result_limit("2000") == 2000


def test_clamp_limit_lower_bound():
    """回归：负数/0 曾直接参与 matches[:limit] 切片，
    matches[:-1] 会静默少给一条且看似成功。"""
    assert clamp_result_limit("0") == 1
    assert clamp_result_limit("-5") == 1


def test_clamp_limit_upper_bound():
    assert clamp_result_limit("99999") == 2000


def test_clamp_limit_garbage_falls_back_to_default():
    assert clamp_result_limit("abc") == 300
    assert clamp_result_limit("") == 300
    assert clamp_result_limit(None) == 300

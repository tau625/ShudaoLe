# -*- coding: utf-8 -*-
"""日志设施：shudaole 统一 logger + 两种 Handler（控制台 / GUI 回调）。

设计约束（P1-3 无感迁移）：
  - 核心模块公开 API 仍接受 on_log 回调（GUI / 老脚本依赖它），不破坏兼容；
  - 没有回调时消息走 logging：默认 NullHandler 静默（库的最佳实践），
    CLI 入口挂上裸格式控制台 Handler 后输出与旧版 print 完全一致；
  - GUI 入口挂 CallbackHandler，任何 shudaole.* 的日志直接进界面日志缓冲，
    实现「GUI 日志与 CLI 日志同源」。
"""
import logging
import sys

_logger = None


def get_logger():
    """返回包级 logger（幂等初始化：INFO 级 + NullHandler，不向 root 传播）。"""
    global _logger
    if _logger is None:
        _logger = logging.getLogger("shudaole")
        _logger.setLevel(logging.INFO)
        _logger.addHandler(logging.NullHandler())
        _logger.propagate = False
    return _logger


class CallbackHandler(logging.Handler):
    """把日志记录转发给回调函数（GUI 的 on_log 门面）。"""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        try:
            self.callback(self.format(record))
        except Exception:
            self.handleError(record)


def attach_callback(callback):
    """给 shudaole logger 挂回调 Handler（GUI 用），返回 handler 便于移除。"""
    h = CallbackHandler(callback)
    get_logger().addHandler(h)
    return h


def attach_console(stream=None):
    """挂裸格式控制台 Handler（CLI 用），输出与旧版 print 一致。"""
    h = logging.StreamHandler(stream or sys.stdout)
    h.setFormatter(logging.Formatter("%(message)s"))
    get_logger().addHandler(h)
    return h

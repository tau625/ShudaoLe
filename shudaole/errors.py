# -*- coding: utf-8 -*-
"""异常类型（下载/鉴权/取消）。"""


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



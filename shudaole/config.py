# -*- coding: utf-8 -*-
"""配置与路径：令牌文件位置、目录缓存位置。"""

import os
from pathlib import Path


def path_is_relative_to(path, base):
    """Path.is_relative_to 的 3.8 兼容实现（该 API Python 3.9 才引入）。"""
    try:
        Path(path).resolve().relative_to(Path(base).resolve())
        return True
    except ValueError:
        return False


def config_dir():
    """用户级配置目录 ~/.config/shudaole/（可用 SHUDAOLE_CONFIG_DIR 覆盖）。"""
    return Path(os.environ.get("SHUDAOLE_CONFIG_DIR")
                or (Path.home() / ".config" / "shudaole"))

def _resolve_token_file():
    """令牌落盘位置：优先用户配置目录 ~/.config/shudaole/token.txt。

    历史版本把 token.txt 放在程序（脚本/exe）同目录，那里可能是共享盘或同步盘，
    明文凭据跟着项目目录走不安全；配置目录是用户私有位置且 POSIX 下可收紧到 600。
    配置目录创建失败（极少见）时退回程序同目录，行为与旧版一致。
    """
    cfg = Path(os.environ.get("SHUDAOLE_CONFIG_DIR") or (Path.home() / ".config" / "shudaole"))
    try:
        cfg.mkdir(parents=True, exist_ok=True)
        return cfg / "token.txt"
    except OSError:
        return Path(__file__).resolve().parent / "token.txt"



TOKEN_FILE = _resolve_token_file()
# 旧位置（程序同目录）：升级后仍可读，避免老用户令牌"消失"

TOKEN_FILE_LEGACY = Path(__file__).resolve().parent / "token.txt"


TOKEN_HELP = (
    "  获取登录令牌的方法:\n"
    "    1) 浏览器登录 https://basic.smartedu.cn\n"
    "    2) 按 F12 打开开发者工具 -> 网络(Network) 标签 -> 过滤框输入 pdf\n"
    "    3) 刷新页面，点击列表中的 pdf.pdf 请求\n"
    "    4) 在 Request Headers(请求标头) 中找到 x-nd-auth，复制其完整值"
)


CATALOG_CACHE = config_dir() / "catalog_cache.json"  # 程序目录可能只读，与令牌同策略放用户配置目录

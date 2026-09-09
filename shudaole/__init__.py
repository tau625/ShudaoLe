# -*- coding: utf-8 -*-
"""书到了（ShudaoLe）核心包。

P1-1 工程化拆分：原单文件 smartedu_downloader.py 按职责拆为
config/errors/net/naming/catalog/download/cli/token/gui 九个模块；
仓库根目录的 smartedu_downloader.py / smartedu_downloader_gui.py /
auto_fetch_token.py 保留为 thin shim，命令行用法与文档零改动。
"""
import sys

# ---------- 控制台 UTF-8 兼容（Windows GBK 控制台不至于乱码崩溃） ----------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass


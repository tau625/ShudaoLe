#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""书到了 · 本地网页界面（thin shim，实现见 shudaole/gui/server.py）

用法不变：
    python smartedu_downloader_gui.py                 # 默认端口 8765
    python smartedu_downloader_gui.py --port 9000     # 指定端口
    python smartedu_downloader_gui.py --no-browser    # 不自动打开浏览器
"""
import sys

from shudaole.gui.server import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

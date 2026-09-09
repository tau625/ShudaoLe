#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键登录获取令牌（thin shim，实现见 shudaole/token.py）

用法不变：
    python auto_fetch_token.py [--token-file <path>] [--timeout <秒>]
"""
import sys

from shudaole.token import main, run_token_fetch  # noqa: E402,F401

if __name__ == "__main__":
    sys.exit(main())

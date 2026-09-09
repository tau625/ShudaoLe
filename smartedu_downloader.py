#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""书到了（ShudaoLe / Book Arrived）· 国家中小学智慧教育平台教材 PDF 下载工具

P1-1 起本文件为 thin shim：实现已拆分至 shudaole 包（shudaole.cli）。
用法与拆分前完全一致（兼容旧文档与旧脚本）：

  python smartedu_downloader.py "https://basic.smartedu.cn/tchMaterial/detail?...contentId=xxxx"
  python smartedu_downloader.py <链接或ID1> <链接或ID2> -o "D:/教材"
  python smartedu_downloader.py -f links.txt -o ./downloads
  python smartedu_downloader.py                 # 不带参数 -> 交互模式

令牌获取方法见 README 或运行时提示。
"""
import sys

from shudaole.cli import main  # noqa: E402

# 向后兼容：老脚本可能 from smartedu_downloader import XXX
from shudaole.errors import (  # noqa: E402,F401
    NeedAuthError, BadContentError, IncompleteDownload, TransientError,
    DownloadError, DetailFetchError, CancelledError,
)
from shudaole.config import TOKEN_FILE, TOKEN_FILE_LEGACY, TOKEN_HELP  # noqa: E402,F401
from shudaole.net import build_session, validate_public_http_url  # noqa: E402,F401
from shudaole.naming import (  # noqa: E402,F401
    parse_content_id, extract_metadata, normalize_title, title_distinct,
    build_filename, sanitize_filename, resolve_dest,
)
from shudaole.catalog import (  # noqa: E402,F401
    fetch_catalog_index, normalize_catalog, search_catalog, catalog_facets,
    publisher_facets, relax_suggestions, dim_match, detail_page_url,
    DIM_LABELS, DIM_ORDERS, FILTER_DIMS, ENABLED_DIMS, SUPPORTED_SCOPE,
    DEFAULT_FILTERS, PUB_GROUPS,
)
from shudaole.download import (  # noqa: E402,F401
    fetch_detail, download_file, verify_pdf, initial_token, AuthContext,
    process_one,
)

if __name__ == "__main__":
    sys.exit(main())

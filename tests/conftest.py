# -*- coding: utf-8 -*-
"""pytest 共享夹具：项目根目录入 sys.path + 常用样例数据。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def sample_detail():
    """模拟平台教材详情 JSON（字段结构参考真实接口）。"""
    return {
        "id": "abc12345-0000-1111-2222-333344445555",
        "title": "义务教育教科书 语文 一年级 上册",
        "tag_list": [
            {"tag_dimension_id": "zxxxd", "tag_name": "小学"},
            {"tag_dimension_id": "zxxnj", "tag_name": "一年级"},
            {"tag_dimension_id": "zxxxk", "tag_name": "语文"},
            {"tag_dimension_id": "zxxbb", "tag_name": "统编版"},
            {"tag_dimension_id": "zxxcc", "tag_name": "上册"},
        ],
        "ti_storages": [
            {"ti_storages": ["https://r1-ndr.ykt.cbern.com.cn/edu_product/esp/assets_document/abc12345.pkg/pdf.pdf"]}
        ],
    }


@pytest.fixture
def sample_items():
    """模拟 normalize_catalog 之后的目录条目（结构化字段已就位）。"""
    return [
        {"id": "id-1", "kind": "textbook", "title": "语文（一年级）上册",
         "title_raw": "义务教育教科书 语文 一年级 上册",
         "phase": "小学", "grade": "一年级", "subject": "语文", "publisher": "统编版",
         "volume": "上册"},
        {"id": "id-2", "kind": "textbook", "title": "数学（七年级）下册",
         "title_raw": "义务教育教科书 数学 七年级 下册",
         "phase": "初中", "grade": "七年级", "subject": "数学", "publisher": "人教版",
         "volume": "下册"},
        {"id": "id-3", "kind": "asset", "title": "英语（五·四学制）三年级上册",
         "title_raw": "英语（五·四学制）三年级上册",
         "phase": "小学", "grade": "三年级", "subject": "英语", "publisher": "人教版（PEP）",
         "volume": "上册"},
    ]

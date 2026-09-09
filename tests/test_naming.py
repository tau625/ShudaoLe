# -*- coding: utf-8 -*-
"""命名/链接解析纯函数测试：parse_content_id、normalize_title、build_filename 等。"""
import pytest

from shudaole.errors import DownloadError
from shudaole.naming import (
    build_filename, extract_metadata, normalize_title, parse_content_id,
    resolve_dest, sanitize_filename, title_distinct,
)


# ---------- parse_content_id ----------

@pytest.mark.parametrize("text,expect", [
    ("ABCDEF01-2345-6789-ABCD-EF0123456789", "abcdef01-2345-6789-abcd-ef0123456789"),
    ("https://basic.smartedu.cn/tchMaterial/detail?contentType=assets_document"
     "&contentId=abc12345-_XYZ&catalogType=tchMaterial", "abc12345-_XYZ"),
    ("contentId=abc12345-_XYZ", "abc12345-_XYZ"),
    ("  contentId=abc12345-_XYZ  ", "abc12345-_XYZ"),
    ("随便一段文字", None),
    ("", None),
    (None, None),
])
def test_parse_content_id(text, expect):
    assert parse_content_id(text) == expect


# ---------- normalize_title ----------

def test_normalize_title_separator_unify():
    # 项目符/半角间隔号统一为 ·，学制写法归一
    assert normalize_title("数学（五•四学制）三年级上册") == "数学（五·四学制）三年级上册"
    assert normalize_title("数学（五四学制）三年级上册") == "数学（五·四学制）三年级上册"


def test_normalize_title_cjk_spaces():
    # 中文字之间的空格删掉；非中文间空格保留
    assert normalize_title("语文  一年级   上册") == "语文一年级上册"
    assert normalize_title("PEP 英语 三年级") == "PEP 英语三年级"  # 拉丁词后空格保留


def test_normalize_title_revision_prefix():
    assert normalize_title("（根据2024年版课程标准修订）语文 一年级 上册") == "语文一年级上册"


def test_normalize_title_empty():
    assert normalize_title("") == ""
    assert normalize_title(None) == ""


# ---------- sanitize_filename ----------

def test_sanitize_filename_replaces_illegal():
    out = sanitize_filename('语文/数学:*?"<>|上册')
    for ch in '\\/:*?"<>|':
        assert ch not in out
    assert out  # 非空


def test_sanitize_filename_strips_dots_and_limits():
    assert sanitize_filename("  .书名.  ") == "书名"
    assert len(sanitize_filename("长" * 300)) == 120
    assert sanitize_filename("") == "未命名教材"
    assert sanitize_filename(None) == "未命名教材"


# ---------- extract_metadata ----------

def test_extract_metadata_from_tags(sample_detail):
    meta = extract_metadata(sample_detail)
    assert meta["phase"] == "小学"
    assert meta["grade"] == "一年级"
    assert meta["subject"] == "语文"
    assert meta["volume"] == "上册"


def test_extract_metadata_empty_detail():
    meta = extract_metadata({})
    assert meta["phase"] == ""
    assert meta["publisher"] == ""


# ---------- title_distinct ----------

def test_title_distinct_strips_structured_fields():
    meta = {"phase": "小学", "subject": "语文", "publisher": "统编版",
            "grade": "一年级", "volume": "上册"}
    assert title_distinct("语文（一年级）上册", meta) == ""
    assert title_distinct("语文（一年级）上册 教师用书", meta) == "教师用书"


# ---------- build_filename ----------

def test_build_filename_structured():
    meta = {"phase": "小学", "subject": "语文", "publisher": "统编版",
            "grade": "一年级", "volume": "上册"}
    assert "小学语文" in build_filename(meta, "义务教育教科书 语文 一年级 上册")
    assert "统编版" in build_filename(meta, "义务教育教科书 语文 一年级 上册")


def test_build_filename_flat_uses_clean_title():
    meta = {"phase": "小学", "subject": "语文"}
    out = build_filename(meta, "义务教育教科书 语文 一年级 上册", flat=True)
    assert out == "语文一年级上册"


# ---------- resolve_dest ----------

def test_resolve_dest_fresh(tmp_path):
    dest, skip = resolve_dest(tmp_path, "小学语文")
    assert dest == tmp_path / "小学语文.pdf"
    assert skip is False


def test_resolve_dest_skip_on_big_file(tmp_path):
    big = tmp_path / "小学语文.pdf"
    big.write_bytes(b"x" * 1_100_000)
    dest, skip = resolve_dest(tmp_path, "小学语文")
    assert skip is True and dest == big


def test_resolve_dest_renames_on_small_file(tmp_path):
    small = tmp_path / "小学语文.pdf"
    small.write_bytes(b"%PDF-")  # <1MB 视为残留，改名续下
    dest, skip = resolve_dest(tmp_path, "小学语文")
    assert skip is False and dest.name == "小学语文(2).pdf"


def test_resolve_dest_reject_escape(tmp_path):
    with pytest.raises(DownloadError):
        resolve_dest(tmp_path, ".." + "\\" + ".." + "\\" + "evil")

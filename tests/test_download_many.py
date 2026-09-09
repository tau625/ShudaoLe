# -*- coding: utf-8 -*-
"""P2-1 并发下载编排测试：download_many（全 mock，无真实网络）。"""
import threading

import pytest
import requests_mock as rm_module

from shudaole import download as dl
from shudaole.download import AuthContext, download_many


@pytest.fixture(autouse=True)
def _skip_dns(monkeypatch):
    """mock 环境无真实 DNS：SSRF 校验放行为公网测试域名。

    P1-4 后 download.py 改用 net.get_with_redirect_check（内部调用
    net 模块的全局 validate），因此补丁要打在 shudaole.net 上。"""
    import shudaole.net as net_mod
    monkeypatch.setattr(net_mod, "validate_public_http_url", lambda url: url)

DETAIL_TMPL = "https://s-file-1.ykt.cbern.com.cn/zxx/ndrv2/resources/tch_material/details/{cid}.json"


def _detail_json(cid, title):
    return {
        "id": cid, "title": title,
        "ti_items": [{"ti_storages": [
            "https://r1-ndr.ykt.cbern.com.cn/edu_product/esp/assets_document/%s.pkg/pdf.pdf" % cid,
        ]}],
        "tag_list": [
            {"tag_dimension_id": "zxxxd", "tag_name": "小学"},
            {"tag_dimension_id": "zxxnj", "tag_name": "一年级"},
            {"tag_dimension_id": "zxxxk", "tag_name": "语文"},
        ],
    }


def _pdf_bytes(n=1200):
    return b"%PDF-1.7\n" + b"0" * n + b"\n%%EOF"


@pytest.fixture
def requests_mock():
    with rm_module.Mocker() as m:
        yield m


def _auth():
    return AuthContext(None, save_token=False, interactive=False)


def test_download_many_concurrent_ok(requests_mock, tmp_path):
    cids = ["%08d-0000-4000-8000-000000000000" % i for i in range(6)]
    for i, cid in enumerate(cids):
        requests_mock.get(DETAIL_TMPL.format(cid=cid), json=_detail_json(cid, "语文教材%d" % i))
        requests_mock.get(
            "https://r1-ndr.ykt.cbern.com.cn/edu_product/esp/assets_document/%s.pkg/pdf.pdf" % cid,
            content=_pdf_bytes())
    entries = ["https://basic.smartedu.cn/tchMaterial/detail?contentType=assets_document&contentId=%s" % c
               for c in cids]
    started = []
    done = []
    results = download_many(entries, tmp_path, _auth(), timeout=5, workers=3,
                            on_item_start=lambda i: started.append(i),
                            on_item_done=lambda i, it: done.append(i))
    assert [r["status"] for r in results] == ["ok"] * 6
    assert sorted(started) == list(range(6))
    assert sorted(done) == list(range(6))
    # 结果顺序与输入对齐
    assert [r["entry"] for r in results] == entries
    # 文件确实落盘
    pdfs = list(tmp_path.glob("*.pdf"))
    assert len(pdfs) == 6


def test_download_many_workers_clamped(requests_mock, tmp_path):
    # workers 超上限 5 会被压回 5；0/负数压回 1——不炸即可
    for i, cid in enumerate(["aaaa0000-0000-4000-8000-000000000000",
                            "bbbb0000-0000-4000-8000-000000000000"]):
        requests_mock.get(DETAIL_TMPL.format(cid=cid), json=_detail_json(cid, "书%d" % i))
        requests_mock.get(
            "https://r1-ndr.ykt.cbern.com.cn/edu_product/esp/assets_document/%s.pkg/pdf.pdf" % cid,
            content=_pdf_bytes())
    entries = [c for c in ("aaaa0000-0000-4000-8000-000000000000",
                         "bbbb0000-0000-4000-8000-000000000000")]
    results = download_many(entries, tmp_path, _auth(), timeout=5, workers=99)
    assert len(results) == 2
    results = download_many(entries, tmp_path, _auth(), timeout=5, workers=0)
    assert len(results) == 2


def test_download_many_cancel_before_start(requests_mock, tmp_path):
    cid = "cccc0000-0000-4000-8000-000000000000"
    requests_mock.get(DETAIL_TMPL.format(cid=cid), json=_detail_json(cid, "书"))
    ev = threading.Event()
    ev.set()  # 开工前就取消
    results = download_many([cid], tmp_path, _auth(), timeout=5,
                            cancel_event=ev)
    assert results[0]["status"] == "fail"
    assert "取消" in results[0]["msg"]


def test_download_many_mixed_statuses(requests_mock, tmp_path):
    # 一条正常、一条坏 ID（详情 404 全端点）
    good = "dddd0000-0000-4000-8000-000000000000"
    bad = "eeee0000-0000-4000-8000-000000000000"
    requests_mock.get(DETAIL_TMPL.format(cid=good), json=_detail_json(good, "好书"))
    requests_mock.get(
        "https://r1-ndr.ykt.cbern.com.cn/edu_product/esp/assets_document/%s.pkg/pdf.pdf" % good,
        content=_pdf_bytes())
    for tmpl in dl.DETAIL_ENDPOINTS:
        requests_mock.get(tmpl.format(cid=bad), status_code=404)
    results = download_many([good, bad], tmp_path, _auth(), timeout=5)
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "fail"

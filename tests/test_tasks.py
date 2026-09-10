# -*- coding: utf-8 -*-
"""任务持久化（P2-2）测试：tasks.json 往返、损坏容忍、未完成判定。"""
import json

from shudaole import tasks


def _sess(**over):
    base = {
        "entries": ["https://a.example/1", "https://a.example/2"],
        "out_dir": "downloads",
        "workers": 3,
        "flat_name": False,
        "items": [
            {"entry": "https://a.example/1", "status": "ok", "msg": "", "file": "x.pdf"},
            {"entry": "https://a.example/2", "status": "downloading",
             "msg": "", "file": ""},
        ],
    }
    base.update(over)
    return base


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "_TASKS_FILE", tmp_path / "tasks.json")
    assert tasks.save_session(_sess()) is True
    loaded = tasks.load_session()
    assert loaded is not None
    assert loaded["entries"] == _sess()["entries"]
    assert loaded["out_dir"] == "downloads"
    assert [it["status"] for it in loaded["items"]] == ["ok", "downloading"]
    assert loaded["saved_at"] > 0          # save_session 落盘时补写时间戳
    # 落盘为合法 JSON（indent 排版不影响解析）
    raw = json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8"))
    assert raw["entries"] == _sess()["entries"]


def test_load_session_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "_TASKS_FILE", tmp_path / "nope.json")
    assert tasks.load_session() is None


def test_load_session_corrupt_returns_none(tmp_path, monkeypatch):
    f = tmp_path / "tasks.json"
    f.write_text("{半截 JSON...", encoding="utf-8")
    monkeypatch.setattr(tasks, "_TASKS_FILE", f)
    assert tasks.load_session() is None


def test_load_session_without_entries_returns_none(tmp_path, monkeypatch):
    f = tmp_path / "tasks.json"
    f.write_text(json.dumps({"items": []}), encoding="utf-8")
    monkeypatch.setattr(tasks, "_TASKS_FILE", f)
    assert tasks.load_session() is None


def test_has_unfinished():
    assert tasks.has_unfinished(_sess()) is True              # 有 downloading
    done = _sess(items=[{"status": "ok"}, {"status": "skip"}, {"status": "fail"}])
    assert tasks.has_unfinished(done) is False
    assert tasks.has_unfinished(None) is False
    assert tasks.has_unfinished({"items": []}) is False


def test_clear_session_removes_file_and_is_idempotent(tmp_path, monkeypatch):
    f = tmp_path / "tasks.json"
    monkeypatch.setattr(tasks, "_TASKS_FILE", f)
    tasks.save_session(_sess())
    assert f.exists()
    tasks.clear_session()
    assert not f.exists()
    tasks.clear_session()   # 文件已不存在时不得抛异常
    assert not f.exists()

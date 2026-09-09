# -*- coding: utf-8 -*-
"""任务持久化（P2-2）：tasks.json 落盘与恢复。

设计：
  - 文件位置 ~/.config/shudaole/tasks.json（与令牌/目录缓存同目录）
  - 只持久化「批量任务会话」：输入链接、输出目录、各项状态/路径/时间戳；
    下载本身的断点由 .part 文件承担（download_file 已支持 Range 续传）
  - 写入用 .tmp + os.replace 原子替换；读失败（损坏/缺失）静默返回 None
  - GUI 启动时发现未完成会话（有非终态条目）→ 提示「继续 / 放弃」
"""
import json
import os
import threading
import time

from .config import config_dir

_TASKS_FILE = None
_LOCK = threading.Lock()


def tasks_file():
    global _TASKS_FILE
    if _TASKS_FILE is None:
        from pathlib import Path
        _TASKS_FILE = Path(config_dir()) / "tasks.json"
    return _TASKS_FILE


def save_session(session):
    """保存任务会话。session: {entries, out_dir, workers, flat_name,
    items: [{entry, title, status, msg, file, downloaded, total}], saved_at}"""
    session = dict(session)
    session["saved_at"] = time.time()
    path = tasks_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(session, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def load_session():
    """读取上次会话；无文件/损坏/已全部终态返回 None。"""
    path = tasks_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("entries"):
        return None
    return data


def has_unfinished(session):
    """会话里是否还有未到终态（ok/skip/fail 之外）的条目。"""
    if not session:
        return False
    for it in session.get("items") or []:
        if it.get("status") not in ("ok", "skip", "fail"):
            return True
    return False


def clear_session():
    """任务全部完成（或用户放弃恢复）后清除持久化。"""
    try:
        tasks_file().unlink()
    except OSError:
        pass

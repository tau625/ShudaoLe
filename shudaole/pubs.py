# -*- coding: utf-8 -*-
"""出版社配置外置（P2-3）。

包内默认值仍是 catalog.PUB_GROUPS（源码即文档）；用户级
~/.config/shudaole/pubs.json 可覆盖/追加，深合并规则：

    {
      "groups": {                       // 追加或覆盖整个分组（值为标签列表）
        "北师大系": ["北师大版", "北京师范大学出版社"],
        "人教版系": ["人教版", "统编版", "人教鄂教版"]   // 覆盖默认组
      },
      "labels": { "北师大系": "北师大系列" }   // 可选：分组显示名
    }

应用时机：GUI/CLI 启动时调用 apply_user_pubs() 一次，模块级
PUB_GROUPS/PUB_GROUP_LABELS/PUB_ORDER 就地更新（DIM_ORDERS 不含 publisher，
排序逻辑用 PUB_ORDER，一并重算）。文件损坏/缺失静默忽略——用户配置失败
不应影响启动。
"""
import json

from .config import config_dir


def _deep_merge_groups(base, override):
    """override 整组覆盖（标签列表不做逐项合并，语义更直观）。"""
    merged = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, list):
            merged[k] = [str(x) for x in v]
    return merged


def user_pubs_path():
    from pathlib import Path
    return Path(config_dir()) / "pubs.json"


def load_user_pubs():
    try:
        return json.loads(user_pubs_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def apply_user_pubs(catalog_mod):
    """把用户配置合并进 catalog 模块的分组表。返回是否有改动。"""
    data = load_user_pubs()
    if not isinstance(data, dict):
        return False
    groups = data.get("groups")
    labels = data.get("labels")
    changed = False
    if isinstance(groups, dict) and groups:
        catalog_mod.PUB_GROUPS = _deep_merge_groups(catalog_mod.PUB_GROUPS, groups)
        catalog_mod.PUB_ORDER = [t for tags in catalog_mod.PUB_GROUPS.values()
                                 for t in tags]
        changed = True
    if isinstance(labels, dict) and labels:
        merged = dict(catalog_mod.PUB_GROUP_LABELS)
        merged.update({str(k): str(v) for k, v in labels.items()})
        catalog_mod.PUB_GROUP_LABELS = merged
        changed = True
    return changed

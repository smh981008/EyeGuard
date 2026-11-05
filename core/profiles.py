# -*- coding: utf-8 -*-
"""配置方案：把当前整套设置存成「配置1 / 配置2 / 上班 / 家里」……

解决的问题：白天上班要开黑屏、阈值设宽，晚上在家只想留着悬浮窗；
每次换场景都去七八个标签页里重新拨一遍滑块，谁都不想干。
于是给整套设置起个名字存下来，用的时候一键套上去。

存储位置就在 config.json 里：

    "profiles": {
        "配置1": {"saved_at": "2026-11-05 15:20", "values": {...整套设置...}},
        "配置2": {...}
    },
    "active_profile": "配置1"

values 里存的是 DEFAULTS 里的全部键（除去 SKIP 那几个），
所以以后加了新设置项，老方案缺的键会自动回退到默认值，不会炸。
"""
from __future__ import annotations

import copy
from datetime import datetime

from core.config import DEFAULTS

# 不进方案的键：
#   config_version / profiles / active_profile —— 配置机制本身，套用会自己吃自己
#   show_preview / enabled —— 运行时临时状态。把它们存进方案的话，
#   哪天你是在暂停状态下存了个方案，以后一套用检测就悄悄关了，很难查。
SKIP = {"config_version", "profiles", "active_profile", "show_preview", "enabled"}


def keys() -> list[str]:
    """参与方案的设置项（顺序跟 DEFAULTS 一致，方便排查）。"""
    return [k for k in DEFAULTS if k not in SKIP]


def _coerce(key: str, value):
    """按默认值的类型把 JSON 里读回来的值掰正。"""
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, list):
        return [int(v) for v in (value or [])]
    if isinstance(default, dict):
        return dict(value or {})
    try:
        return type(default)(value)
    except (TypeError, ValueError):
        return default


def snapshot(cfg) -> dict:
    """把当前设置拍一张快照（深拷贝，之后改设置不会串味）。"""
    return {k: copy.deepcopy(cfg.get(k, DEFAULTS[k])) for k in keys()}


def names(cfg) -> list[str]:
    """已保存的方案名，按保存时间从新到旧；同一分钟内保存的保持创建顺序。"""
    book = cfg.get("profiles") or {}
    # sorted 是稳定排序，时间戳打平时不会把 配置1/配置2 的顺序打乱
    return [
        name for name, _ in sorted(
            book.items(),
            key=lambda kv: str((kv[1] or {}).get("saved_at", "")),
            reverse=True,
        )
    ]


def exists(cfg, name: str) -> bool:
    return name in (cfg.get("profiles") or {})


def entry(cfg, name: str) -> dict:
    return (cfg.get("profiles") or {}).get(name) or {}


def save(cfg, name: str, overwrite: bool = True) -> bool:
    """把当前设置存成/覆盖 name。返回是否成功。"""
    name = (name or "").strip()
    if not name:
        return False
    book = dict(cfg.get("profiles") or {})
    if name in book and not overwrite:
        return False
    book[name] = {
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "values": snapshot(cfg),
    }
    cfg["profiles"] = book
    cfg["active_profile"] = name
    return True


def apply(cfg, name: str) -> bool:
    """把方案里的值整套写回 cfg。缺的键保持原样（新版本新增的设置项走默认）。"""
    values = entry(cfg, name).get("values") or {}
    if not values:
        return False
    for key, value in values.items():
        if key in DEFAULTS and key not in SKIP:
            try:
                cfg[key] = _coerce(key, value)
            except Exception:
                continue
    cfg["active_profile"] = name
    return True


def rename(cfg, old: str, new: str) -> bool:
    new = (new or "").strip()
    book = dict(cfg.get("profiles") or {})
    if not new or old not in book or new == old or new in book:
        return False
    # 保持顺序：重建一遍字典，把名字换掉
    cfg["profiles"] = {(new if k == old else k): v for k, v in book.items()}
    if cfg.get("active_profile") == old:
        cfg["active_profile"] = new
    return True


def delete(cfg, name: str) -> bool:
    book = dict(cfg.get("profiles") or {})
    if name not in book:
        return False
    book.pop(name)
    cfg["profiles"] = book
    if cfg.get("active_profile") == name:
        cfg["active_profile"] = ""
    return True


def next_name(cfg) -> str:
    """新建时的默认名字：配置1、配置2……挑第一个没被占用的。"""
    book = cfg.get("profiles") or {}
    i = 1
    while f"配置{i}" in book:
        i += 1
    return f"配置{i}"


def describe(cfg, name: str) -> str:
    """一行摘要，让人在列表里一眼认出来这是哪个场景。"""
    values = entry(cfg, name).get("values") or {}
    bits: list[str] = []

    monthly = 0.0
    try:
        monthly = float(values.get("pay_monthly", 0) or 0)
    except (TypeError, ValueError):
        monthly = 0.0
    if monthly > 0:
        bits.append(f"月薪 {monthly:,.0f}")

    start, end = values.get("pay_start"), values.get("pay_end")
    if start and end:
        span = f"{start}–{end}"
        if values.get("pay_break_enabled") and values.get("pay_start2"):
            span += f" + {values.get('pay_start2')}–{values.get('pay_end2')}"
        bits.append(span)

    if values.get("schedule_enabled"):
        bits.append(f"限时运行 {values.get('schedule_start')}–{values.get('schedule_end')}")

    if not values.get("action_black_screen", True):
        acts = [n for k, n in (
            ("action_switch_window", "切窗口"),
            ("action_show_desktop", "显示桌面"),
            ("action_lock", "锁屏"),
            ("action_monitor_off", "关屏"),
        ) if values.get(k)]
        bits.append("、".join(acts) if acts else "不执行动作")

    if values.get("check_head_pose"):
        bits.append("转头也算离开")

    return " · ".join(bits) if bits else "还没填薪资/时段"

# -*- coding: utf-8 -*-
"""定时计划：只在设定的星期与时间段内运行检测。

为什么单独一个模块：时间段的边界判断比看上去容易写错——跨午夜（22:00~06:00
这种）、"周日晚上算周日还是算周一"、时间填坏了怎么办。这些分支集中在这里，
主循环和界面都只问一句 in_window()。

跨午夜的星期归属：以**时段开始那天**为准。22:00~06:00 且勾了周一时，周一
22:00 到周二 06:00 属于这个时段；周二晚上 22:00 不属于。
"""
from __future__ import annotations

from datetime import datetime

DAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
DAY_SHORT = ["一", "二", "三", "四", "五", "六", "日"]


def parse_hhmm(text: str) -> int | None:
    """把 "09:30" 解析成一天中的分钟数，非法返回 None。"""
    try:
        raw = str(text).strip().replace("：", ":")
        hh, mm = raw.split(":")[:2]
        h, m = int(hh), int(mm)
    except (TypeError, ValueError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def days_set(cfg) -> set[int]:
    """勾选的星期集合，周一 = 0。一项都没勾时按"每天"处理，避免把软件
    锁成永远不运行这种灾难性配置。"""
    out: set[int] = set()
    try:
        for item in cfg.get("schedule_days", []):
            d = int(item)
            if 0 <= d <= 6:
                out.add(d)
    except (TypeError, ValueError):
        pass
    return out or set(range(7))


def in_window(cfg, now: datetime | None = None) -> bool:
    """当前是否处在计划时段内。计划关掉时永远返回 True。"""
    if not cfg.get("schedule_enabled", False):
        return True

    start = parse_hhmm(cfg.get("schedule_start", ""))
    end = parse_hhmm(cfg.get("schedule_end", ""))
    if start is None or end is None:
        return True                      # 时间填坏了：当作全天，别乱停
    if start == end:
        return True                      # 起止相同：视为全天

    now = now or datetime.now()
    cur = now.hour * 60 + now.minute
    days = days_set(cfg)

    if start < end:
        return start <= cur < end and now.weekday() in days

    # 跨午夜
    if cur >= start:
        return now.weekday() in days
    if cur < end:
        return (now.weekday() - 1) % 7 in days
    return False


def days_label(days: set[int] | list[int]) -> str:
    ds = sorted(days)
    if len(ds) == 7:
        return "每天"
    if ds == [0, 1, 2, 3, 4]:
        return "工作日"
    if ds == [5, 6]:
        return "周末"
    if not ds:
        return "未选择"
    return "、".join(DAY_NAMES[d] for d in ds)


def summary(cfg) -> str:
    """给人看的一句话描述，例如「工作日 09:00–18:00」。"""
    start = cfg.get("schedule_start", "")
    end = cfg.get("schedule_end", "")
    return f"{days_label(days_set(cfg))} {start}–{end}"


def describe_now(cfg, now: datetime | None = None) -> str:
    """当前状态的一句话说明。"""
    if not cfg.get("schedule_enabled", False):
        return "未启用定时计划，检测始终按开关状态运行"
    if in_window(cfg, now):
        return f"现在在计划时段内（{summary(cfg)}），检测会正常运行"
    return f"现在不在计划时段内（{summary(cfg)}），检测已自动暂停"


if __name__ == "__main__":  # 手动验证
    cases = [
        ("09:00", "18:00", [0, 1, 2, 3, 4], datetime(2026, 11, 2, 10, 0), True),   # 周一上午
        ("09:00", "18:00", [0, 1, 2, 3, 4], datetime(2026, 11, 2, 19, 0), False),  # 周一晚上
        ("09:00", "18:00", [0, 1, 2, 3, 4], datetime(2026, 11, 7, 10, 0), False),  # 周六
        ("22:00", "06:00", [0], datetime(2026, 11, 2, 23, 0), True),               # 周一深夜
        ("22:00", "06:00", [0], datetime(2026, 11, 3, 5, 0), True),                # 周二凌晨(算周一)
        ("22:00", "06:00", [0], datetime(2026, 11, 3, 22, 30), False),             # 周二深夜
    ]
    ok = True
    for s, e, d, now, want in cases:
        cfg = {"schedule_enabled": True, "schedule_start": s, "schedule_end": e,
               "schedule_days": d}
        got = in_window(cfg, now)
        ok &= got == want
        print(("PASS" if got == want else "FAIL"), s, e, DAY_NAMES[now.weekday()],
              now.strftime("%H:%M"), "->", got)
    print("全部通过" if ok else "有失败")

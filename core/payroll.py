# -*- coding: utf-8 -*-
"""上班鼓励器：把月薪换算成时薪，实时算出今天已经赚了多少、还剩多久下班。

换算口径：
    日薪 = 月薪 ÷ 每月计薪天数
    时薪 = 日薪 ÷ 每日在岗小时

"每日在岗小时"按上下班时段累加，支持中间挖掉一段（午休）：
    08:30–11:30 + 14:00–17:30  ->  3 + 3.5 = 6.5 小时

午休那段时间不产生收入，进度条会停住、金额不动 —— 这样"今天赚了多少"始终
等于"你实际干活干了多久"，比把午休也算成工时更经得起推敲。

所有钱都按当前这一刻连续折算，不做"整点跳一次"，所以黑屏上的数字会一秒
一秒往上走。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

STATUS_UNSET = "unset"      # 还没填月薪
STATUS_BEFORE = "before"    # 工作日，还没到上班时间
STATUS_WORKING = "working"  # 正在上班
STATUS_LUNCH = "lunch"      # 两个时段之间的休息（午休）
STATUS_AFTER = "after"      # 已下班，今天的钱赚满了
STATUS_OFFDAY = "offday"    # 今天不是工作日

WEEKDAY_NAMES = ["一", "二", "三", "四", "五", "六", "日"]


# ------------------------------------------------------------------ 小工具
def _num(value, fallback: float = 0.0) -> float:
    """把配置里的值转成能用的 float，坏数据一律回退。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return fallback
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
        return fallback
    return v


def parse_hhmm(text, fallback: str = "09:00") -> time:
    """解析 "HH:MM"。填坏了就退回 fallback，绝不抛异常。"""
    for src in (text, fallback):
        try:
            parts = str(src).strip().split(":")
            h, m = int(parts[0]), int(parts[1])
            if 0 <= h < 24 and 0 <= m < 60:
                return time(h, m)
        except (TypeError, ValueError, IndexError):
            continue
    return time(9, 0)


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _span(a: time, b: time) -> int:
    """a 到 b 的分钟数；b 早于 a 视为跨夜。起止相同算 0。"""
    m1, m2 = _minutes(a), _minutes(b)
    if m1 == m2:
        return 0
    if m2 < m1:
        m2 += 24 * 60
    return m2 - m1


def segments(cfg) -> list[tuple[time, time]]:
    """当天的在岗时段列表。分段开关关掉时就是一段。"""
    s1 = parse_hhmm(cfg.get("pay_start"), "09:00")
    e1 = parse_hhmm(cfg.get("pay_end"), "18:00")
    out = [(s1, e1)]

    if bool(cfg.get("pay_break_enabled", False)):
        s2 = parse_hhmm(cfg.get("pay_start2"), "14:00")
        e2 = parse_hhmm(cfg.get("pay_end2"), "17:30")
        # 只有"下午开始晚于上午结束、且下午结束晚于下午开始"才认第二段，
        # 否则说明时间填岔了，退回一段，绝不算出负数工时。
        if _minutes(s2) > _minutes(e1) and _minutes(e2) > _minutes(s2):
            out.append((s2, e2))
    return out


def work_days(cfg) -> set[int]:
    """工作日集合，周一 = 0。一天都没勾按"每天"处理。"""
    raw = cfg.get("pay_days") or []
    days: set[int] = set()
    for d in raw:
        try:
            d = int(d)
        except (TypeError, ValueError):
            continue
        if 0 <= d <= 6:
            days.add(d)
    return days or set(range(7))


def month_days(cfg) -> float:
    """每月计薪天数。5 天工作制约 21.75，6 天制（单休）约 26。"""
    v = _num(cfg.get("pay_month_days"), 21.75)
    return v if 1.0 <= v <= 31.0 else 21.75


def hours_per_day(cfg) -> float:
    """每日在岗小时 = 各时段之和（午休不算）。"""
    total = sum(_span(a, b) for a, b in segments(cfg)) / 60.0
    return total if total > 0 else 8.0


# ------------------------------------------------------------------ 快照
@dataclass
class PaySnapshot:
    status: str
    monthly: float
    daily: float
    hourly: float
    start: time
    end: time
    days: int              # 每月计薪天数
    hours: float           # 每日在岗小时
    earned: float = 0.0    # 今天已经赚到（元）
    remain: float = 0.0    # 距离下班还有多少"要干活"的秒数
    progress: float = 0.0  # 今天走完的比例 0~1

    @property
    def ok(self) -> bool:
        return self.status != STATUS_UNSET

    @property
    def per_minute(self) -> float:
        return self.hourly / 60.0


def _intervals(day: date, segs: list[tuple[time, time]]) -> list[tuple[datetime, datetime]]:
    """把某一天的时段落成具体区间；跨夜的段落到第二天。"""
    out = []
    for a, b in segs:
        start = datetime.combine(day, a)
        end_day = day + timedelta(days=1) if _minutes(b) <= _minutes(a) else day
        out.append((start, datetime.combine(end_day, b)))
    return out


def _evaluate(
    ivs: list[tuple[datetime, datetime]], now: datetime,
    daily: float, base: dict,
) -> PaySnapshot:
    total = sum((b - a).total_seconds() for a, b in ivs) or 1.0
    elapsed = 0.0
    for a, b in ivs:
        if now >= b:
            elapsed += (b - a).total_seconds()
        elif now > a:
            elapsed += (now - a).total_seconds()
    frac = min(1.0, max(0.0, elapsed / total))
    inside = any(a <= now < b for a, b in ivs)

    if now < ivs[0][0]:
        status = STATUS_BEFORE
    elif now >= ivs[-1][1]:
        status = STATUS_AFTER
    elif inside:
        status = STATUS_WORKING
    else:
        status = STATUS_LUNCH

    return PaySnapshot(
        status=status, earned=daily * frac,
        remain=max(0.0, total - elapsed), progress=frac, **base,
    )


def snapshot(cfg, now: datetime | None = None) -> PaySnapshot:
    """算出此刻的薪资快照。没填月薪时 status = unset。"""
    now = now or datetime.now()
    monthly = _num(cfg.get("pay_monthly"), 0.0)
    mdays = month_days(cfg)
    segs = segments(cfg)
    hours = hours_per_day(cfg)

    # 日薪保留 3 位（到厘）：月薪 ÷ 21.75 是无限小数，先定格再往后乘，
    # 否则界面上显示的日薪和由它推出来的时薪、已赚金额会出现尾数对不上。
    daily = round(monthly / mdays, 3) if monthly > 0 else 0.0
    hourly = daily / hours if daily > 0 else 0.0

    base = dict(
        monthly=monthly, daily=daily, hourly=hourly,
        start=segs[0][0], end=segs[-1][1], days=mdays, hours=hours,
    )
    if monthly <= 0:
        return PaySnapshot(status=STATUS_UNSET, **base)

    days = work_days(cfg)
    today = now.date()

    # 昨天开的班可能还没结束（跨夜），先看它
    yesterday = today - timedelta(days=1)
    if yesterday.weekday() in days:
        ivs = _intervals(yesterday, segs)
        if ivs[0][0] <= now < ivs[-1][1]:
            return _evaluate(ivs, now, daily, base)

    if today.weekday() in days:
        return _evaluate(_intervals(today, segs), now, daily, base)

    return PaySnapshot(status=STATUS_OFFDAY, **base)


# ------------------------------------------------------------------ 文案
def money(v: float, places: int = 2) -> str:
    """金额格式化。实发金额到分为止（2 位），换算出来的日薪到厘（3 位）。"""
    return f"¥{v:,.{places}f}"


def money_daily(v: float) -> str:
    """日薪专用：10000 ÷ 21.75 = 459.770，2 位小数会把厘丢掉。"""
    return money(v, 3)


def human_duration(seconds: float) -> str:
    s = int(max(0.0, seconds))
    h, m = s // 3600, (s % 3600) // 60
    if h and m:
        return f"{h} 小时 {m} 分"
    if h:
        return f"{h} 小时"
    if m:
        return f"{m} 分钟"
    return "不到 1 分钟"


def countdown_text(snap: PaySnapshot) -> str:
    """黑屏上那行倒计时 / 状态。"""
    if snap.status == STATUS_WORKING:
        return f"距下班 {human_duration(snap.remain)}"
    if snap.status == STATUS_LUNCH:
        return f"午休中 · 距下班 {human_duration(snap.remain)}"
    if snap.status == STATUS_BEFORE:
        return f"还没开工 · {snap.start.strftime('%H:%M')} 开始"
    if snap.status == STATUS_AFTER:
        return "已下班 · 今天的钱赚满了"
    if snap.status == STATUS_OFFDAY:
        return "今天休息 · 明天继续"
    return "到设置里填一下月薪"


def rate_line(snap: PaySnapshot) -> str:
    return f"时薪 {money(snap.hourly)} · 每分钟 {money(snap.per_minute)}"


def schedule_line(cfg) -> str:
    segs = segments(cfg)
    body = " + ".join(f"{a.strftime('%H:%M')}–{b.strftime('%H:%M')}" for a, b in segs)
    if len(segs) == 2:
        return f"{body}，中间休息 {segs[0][1].strftime('%H:%M')}–{segs[1][0].strftime('%H:%M')}"
    return body


def summary(cfg, now: datetime | None = None) -> str:
    """给设置页看的一句话摘要。"""
    snap = snapshot(cfg, now)
    if not snap.ok:
        return "还没填月薪，黑屏上不会显示。"
    return (
        f"时段 {schedule_line(cfg)}，共 {snap.hours:g} 小时。\n"
        f"日薪 {money_daily(snap.daily)} = {money(snap.monthly)} ÷ {snap.days:g} 天；"
        f"时薪 {money(snap.hourly)} = 日薪 ÷ {snap.hours:g} 小时。\n"
        f"现在是「{countdown_text(snap)}」，今天已赚 {money(snap.earned)}。"
    )


# ------------------------------------------------------------------ 自测
if __name__ == "__main__":
    # 2026-11-03 是周二，11-07 是周六
    base = {
        "pay_monthly": 10000, "pay_month_days": 21.75,
        "pay_start": "08:30", "pay_end": "11:30",
        "pay_break_enabled": True, "pay_start2": "14:00", "pay_end2": "17:30",
        "pay_days": [0, 1, 2, 3, 4],
    }

    def show(label: str, cfg: dict, when: datetime) -> None:
        s = snapshot(cfg, when)
        print(
            f"{label:<24} {when:%m-%d %a %H:%M}  {s.status:<8} "
            f"earned={s.earned:>8.2f}  remain={int(s.remain)//60:>4}min  "
            f"prog={s.progress:5.1%}  hours={s.hours:g}  |  {countdown_text(s)}"
        )

    print("分段:", schedule_line(base), "| 每日在岗", hours_per_day(base), "小时")
    s0 = snapshot(base, datetime(2026, 11, 3, 10, 0))
    print(
        f"日薪 {money_daily(s0.daily)} = {money(s0.monthly)} ÷ {s0.days:g} 天"
        f"  （未截断 {s0.monthly / s0.days:.6f}）"
    )
    print(f"时薪 {money(s0.hourly)} = 日薪 ÷ {s0.hours:g} 小时  （{s0.hourly:.6f}）")
    show("08:00 未开工", base, datetime(2026, 11, 3, 8, 0))
    show("08:30 上班", base, datetime(2026, 11, 3, 8, 30))
    show("10:00 上午段", base, datetime(2026, 11, 3, 10, 0))
    show("11:30 午休开始", base, datetime(2026, 11, 3, 11, 30))
    show("12:40 午休中", base, datetime(2026, 11, 3, 12, 40))
    show("14:00 下午段", base, datetime(2026, 11, 3, 14, 0))
    show("15:45 下午段", base, datetime(2026, 11, 3, 15, 45))
    show("17:30 下班", base, datetime(2026, 11, 3, 17, 30))
    show("周六 休息", base, datetime(2026, 11, 7, 12, 0))

    print()
    # 分段关掉时，中间的时间算在岗
    one = dict(base, pay_break_enabled=False, pay_start="08:30", pay_end="17:30")
    s = snapshot(one, datetime(2026, 11, 3, 12, 40))
    print(f"不分段 12:40 -> {s.status} hours={s.hours:g} earned={s.earned:.2f}")

    # 跨夜单段仍然可用
    night = {"pay_monthly": 10000, "pay_month_days": 21.75,
             "pay_start": "22:00", "pay_end": "06:00", "pay_break_enabled": False,
             "pay_days": [0]}
    show("跨夜 周二02:00", night, datetime(2026, 11, 3, 2, 0))
    show("跨夜 周一23:00", night, datetime(2026, 11, 2, 23, 0))

    # 下午时间填岔了（开始早于上午结束）应当退回一段
    bad = dict(base, pay_start2="11:00")
    print("填岔时退化为:", schedule_line(bad), "|", hours_per_day(bad), "小时")

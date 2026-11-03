# -*- coding: utf-8 -*-
"""上班鼓励器：把月薪换算成时薪，实时算出今天已经赚了多少、还剩多久下班。

换算口径：
    日薪 = 月薪 ÷ 每月计薪天数
    时薪 = 日薪 ÷ 每日在岗小时（= 下班时间 - 上班时间）

"在岗小时"就是打卡时段本身，午休也算在里面 —— 这样"今天赚了多少"等于
"你在工位上坐了多久"，看数字涨才符合直觉。想按实际工时算，把上班时间往后
调（比如 09:00 改成 10:00）或者直接调小每日区间即可。

所有钱都按当前这一刻连续折算，不做"整点跳一次"，所以黑屏上的数字会一秒
一秒往上走。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

STATUS_UNSET = "unset"      # 还没填月薪
STATUS_BEFORE = "before"    # 工作日，还没到上班时间
STATUS_WORKING = "working"  # 正在上班
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
    """每日在岗小时。下班早于上班视为跨夜。"""
    s = parse_hhmm(cfg.get("pay_start"), "09:00")
    e = parse_hhmm(cfg.get("pay_end"), "18:00")
    sm, em = s.hour * 60 + s.minute, e.hour * 60 + e.minute
    if em == sm:
        return 8.0                     # 起止相同没法算，按 8 小时兜底
    if em < sm:
        em += 24 * 60                  # 跨夜
    return (em - sm) / 60.0


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
    remain: float = 0.0    # 距离下班还有多少秒
    progress: float = 0.0  # 今天走完的比例 0~1

    @property
    def ok(self) -> bool:
        return self.status != STATUS_UNSET

    @property
    def per_minute(self) -> float:
        return self.hourly / 60.0


def snapshot(cfg, now: datetime | None = None) -> PaySnapshot:
    """算出此刻的薪资快照。没填月薪时 status = unset。"""
    now = now or datetime.now()
    monthly = _num(cfg.get("pay_monthly"), 0.0)
    mdays = month_days(cfg)
    hours = hours_per_day(cfg)
    s = parse_hhmm(cfg.get("pay_start"), "09:00")
    e = parse_hhmm(cfg.get("pay_end"), "18:00")

    daily = monthly / mdays if monthly > 0 else 0.0
    hourly = daily / hours if daily > 0 else 0.0

    base = dict(
        monthly=monthly, daily=daily, hourly=hourly,
        start=s, end=e, days=mdays, hours=hours,
    )
    if monthly <= 0:
        return PaySnapshot(status=STATUS_UNSET, **base)

    days = work_days(cfg)
    cross = (e.hour * 60 + e.minute) <= (s.hour * 60 + s.minute)
    today = now.date()

    def shift_of(day: date) -> tuple[datetime, datetime]:
        start_dt = datetime.combine(day, s)
        end_day = day + timedelta(days=1) if cross else day
        return start_dt, datetime.combine(end_day, e)

    # 先找"此刻正在进行"的班次：跨夜的班次昨天开的，现在还在上。
    for day in (today - timedelta(days=1), today):
        if day.weekday() not in days:
            continue
        st, en = shift_of(day)
        if st <= now < en:
            span = (en - st).total_seconds() or 1.0
            frac = (now - st).total_seconds() / span
            frac = min(1.0, max(0.0, frac))
            return PaySnapshot(
                status=STATUS_WORKING, earned=daily * frac, remain=(en - now).total_seconds(),
                progress=frac, **base,
            )

    # 没有进行中的班次：看今天本身有没有班
    if today.weekday() in days:
        st, en = shift_of(today)
        if now < st:
            return PaySnapshot(status=STATUS_BEFORE, remain=(en - st).total_seconds(), **base)
        return PaySnapshot(status=STATUS_AFTER, earned=daily, progress=1.0, **base)

    return PaySnapshot(status=STATUS_OFFDAY, **base)


# ------------------------------------------------------------------ 文案
def money(v: float) -> str:
    return f"¥{v:,.2f}"


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
    if snap.status == STATUS_BEFORE:
        return f"还没开工 · {snap.start.strftime('%H:%M')} 开始"
    if snap.status == STATUS_AFTER:
        return "已下班 · 今天的钱赚满了"
    if snap.status == STATUS_OFFDAY:
        return "今天休息 · 明天继续"
    return "到设置里填一下月薪"


def rate_line(snap: PaySnapshot) -> str:
    return f"时薪 {money(snap.hourly)} · 每分钟 {money(snap.per_minute)}"


def summary(cfg, now: datetime | None = None) -> str:
    """给设置页看的一句话摘要。"""
    snap = snapshot(cfg, now)
    if not snap.ok:
        return "还没填月薪，黑屏上不会显示。"
    return (
        f"日薪 {money(snap.daily)} = {money(snap.monthly)} ÷ {snap.days:g} 天；"
        f"时薪 {money(snap.hourly)} = 日薪 ÷ {snap.hours:g} 小时。\n"
        f"现在是「{countdown_text(snap)}」，今天已赚 {money(snap.earned)}。"
    )


# ------------------------------------------------------------------ 自测
if __name__ == "__main__":
    base = {
        "pay_monthly": 17400, "pay_month_days": 21.75,
        "pay_start": "09:00", "pay_end": "18:00", "pay_days": [0, 1, 2, 3, 4],
    }

    def show(label: str, cfg: dict, when: datetime) -> None:
        s = snapshot(cfg, when)
        print(
            f"{label:<26} {when:%m-%d %a %H:%M}  "
            f"{s.status:<8} earned={s.earned:>8.2f}  remain={int(s.remain):>6}s  "
            f"prog={s.progress:5.1%}  |  {countdown_text(s)}"
        )

    # 2026-11-03 是周二，11-07 是周六
    show("工作日 08:00 未开工", base, datetime(2026, 11, 3, 8, 0))
    show("工作日 09:00 整点开工", base, datetime(2026, 11, 3, 9, 0))
    show("工作日 13:30 上到一半", base, datetime(2026, 11, 3, 13, 30))
    show("工作日 18:00 整点下班", base, datetime(2026, 11, 3, 18, 0))
    show("工作日 20:00 已下班", base, datetime(2026, 11, 3, 20, 0))
    show("周六 全天休息", base, datetime(2026, 11, 7, 14, 0))

    night = dict(base, pay_start="22:00", pay_end="06:00", pay_days=[0])
    show("跨夜(周一班) 周二 02:00", night, datetime(2026, 11, 3, 2, 0))
    show("跨夜(周一班) 周一 23:00", night, datetime(2026, 11, 2, 23, 0))
    show("跨夜(周一班) 周二 12:00", night, datetime(2026, 11, 3, 12, 0))

    unset = dict(base, pay_monthly=0)
    print("未填月薪:", snapshot(unset, datetime(2026, 11, 3, 10, 0)).status)
    print("时薪校验: 17400/21.75/9 =", round(snapshot(base, datetime(2026, 11, 3, 10, 0)).hourly, 2))

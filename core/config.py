# -*- coding: utf-8 -*-
"""配置管理：默认值、读写 config.json、资源路径定位。"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

APP_NAME = "视线守卫"
APP_VERSION = "1.5.0"


def app_root() -> Path:
    """程序所在目录。打包成 exe 后为 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(rel: str) -> Path:
    """只读资源路径，兼容 PyInstaller 单文件打包。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / rel
    return app_root() / rel


# ---------------------------------------------------------------- 默认配置
DEFAULTS = {
    # 运行
    "enabled": True,
    "camera_index": 0,
    "camera_width": 640,
    "camera_height": 480,
    "detect_interval_ms": 120,      # 每隔多久做一次检测
    "start_with_windows": False,

    # 时间防抖
    "leave_delay": 2.5,             # 离开持续多久才触发动作（秒）
    "return_delay": 1.0,            # 回来持续多久才恢复（秒）

    # 判定条件
    "check_face_missing": True,     # 画面里没有人脸 -> 离开
    "check_face_size": True,        # 人脸太小（坐远了/走开了）
    "min_face_ratio": 0.12,         # 人脸宽度 / 画面宽度 的下限
    # 头部偏转默认关：5 个关键点分不清"看副屏"和"人走了"。开着它，
    # 只要你侧头看副屏/手机就会被判成离开，这是最招人烦的误判来源。
    # 需要"视线移开就黑屏"的激进效果时才打开。
    "check_head_pose": False,
    "yaw_threshold": 65.0,          # 左右转头阈值（度），超过才算转开
                                     # 定得高：看副屏/看键盘时
                                     # 头会偏 30~60°，不该算离开
    "pitch_threshold": 55.0,        # 抬头低头阈值（度）
    "check_center": False,          # 人脸偏离画面中心
    "center_threshold": 0.28,       # 归一化距离上限（0~1）

    # 键鼠活动闸门：正在操作电脑时绝不判定离开（防误黑屏）
    "check_activity": True,
    "activity_window": 8.0,         # 多久内有键鼠输入算"正在用"（秒）

    # 校准基准：正对屏幕时鼻尖相对眼中点的归一化偏移
    "calibrated": False,
    "base_yaw_i": 0.0,
    "base_pitch_i": 0.69,

    # 触发动作（可多选）
    "action_black_screen": True,
    "action_switch_window": False,
    "action_show_desktop": False,
    "action_lock": False,
    "action_monitor_off": False,

    # 黑屏外观
    "black_screen_text": "已离开 · 回来即恢复",
    "black_screen_hint": "按任意键 / 移动鼠标即可继续",

    # 定时计划：只在设定的星期与时间段内运行检测
    "schedule_enabled": False,
    "schedule_start": "09:00",
    "schedule_end": "18:00",
    "schedule_days": [0, 1, 2, 3, 4],   # 周一 = 0；空列表按"每天"处理

    # 黑屏解除方式
    "wake_on_input": True,          # 任意键/鼠标即解除黑屏（False 则只能按 ESC）
    "wake_rearm_seconds": 0.0,      # 解除后多久没键鼠输入且人仍不在，重新黑屏
                                    # 0 = 不重新黑屏（下次真正离开才再触发）

    # 上班鼓励器：独立桌面悬浮窗，实时显示"今天赚了多少、还剩多久下班"。
    # 跟检测/黑屏完全解耦——黑屏关了它也照常待在桌面上。
    "pay_enabled": True,
    "pay_widget_x": -1,             # 悬浮窗位置；-1 = 还没摆过，贴右下角
    "pay_widget_y": -1,
    "pay_widget_locked": False,     # 锁定位置：拖不动，防手滑
    "pay_widget_autohide": False,   # 平时整窗透明，鼠标移进范围才淡入
    # 外观：色相/饱和度无级可调，底色是同一色相的极暗版本，整窗色调跟着走
    "pay_widget_hue": 36,           # 色相 0–359（36° + 62% 饱和就是原来的金色）
    "pay_widget_sat": 62,           # 饱和度 0–100；0 = 纯灰白
    "pay_widget_bg_alpha": 92,      # 底板不透明度 0–100（0 = 完全透出桌面）
    "pay_widget_text_alpha": 100,   # 文字/刻度不透明度 0–100
    # 点击穿透：窗口盖在别的东西上面时，能不能透过去点底下的页面
    #   0 = 不穿透（正常点它）
    #   1 = 按住 Ctrl 时穿透（松开就恢复正常，随时能拖动/关闭）
    #   2 = 始终穿透（纯看板，只能从托盘菜单改回来）
    "pay_widget_clickthrough": 0,
    "pay_monthly": 0.0,             # 月薪（元）；0 = 还没填，悬浮窗提示去设置
    "pay_month_days": 21.75,        # 每月计薪天数：5 天工作制 21.75，单休约 26
    "pay_start": "09:00",           # 上午上班
    "pay_end": "18:00",             # 上午下班（不分段时就是全天结束）
    "pay_days": [0, 1, 2, 3, 4],    # 周一 = 0；空列表按"每天"处理

    # 午休分段：08:30–11:30 + 14:00–17:30，中间那段不计工时
    "pay_break_enabled": False,
    "pay_start2": "14:00",
    "pay_end2": "17:30",

    # 其他
    "show_preview": False,
    "preview_mirror": True,
    "sound_enabled": False,

    # 配置方案：整套设置存成一个个有名字的方案（配置1、配置2…）
    #   {"配置1": {"saved_at": "2026-11-05 15:20", "values": {...}}, ...}
    # values 里是 DEFAULTS 全部键的快照，详见 core/profiles.py
    "profiles": {},
    "active_profile": "",           # 最近一次保存/套用的方案名

    # 配置结构版本，用于升级时修正历史遗留的极端参数
    "config_version": 10,
}

CONFIG_VERSION = 10

# v2 时期的黑屏提示文案，升级时替换掉
_OLD_HINT = "按 ESC 可手动关闭"

# 旧版本里这些值一旦被拖到极端，软件会"一用就黑屏"或者"一亮一灭"：
#   转头阈值 10°  -> 正常坐姿都被判成转开
#   恢复延迟 0.2s -> 一帧读不出朝向就把黑屏掀开
# 升级时把它们拉回安全范围，避免用户不知道自己在跟什么参数较劲。
_MINIMUMS = {
    "yaw_threshold": 50.0,
    "pitch_threshold": 40.0,
    "leave_delay": 1.5,
    "return_delay": 0.8,
}


def _migrate(data: dict) -> dict:
    try:
        version = int(data.get("config_version", 1))
    except (TypeError, ValueError):
        version = 1
    if version >= CONFIG_VERSION:
        return data

    if version < 2:
        for key, low in _MINIMUMS.items():
            try:
                data[key] = max(float(data.get(key, DEFAULTS[key])), low)
            except (TypeError, ValueError):
                data[key] = DEFAULTS[key]
        data.setdefault("check_activity", True)
        data.setdefault("activity_window", 8.0)
        # 旧版本默认开着头部偏转，正是误判的主要来源，升级时统一关掉
        data["check_head_pose"] = False

    if version < 3:
        # v3 起支持"按任意键 / 动鼠标解除黑屏"，旧文案要跟着换，
        # 否则用户还在盯着屏幕上"按 ESC"发呆，以为只能用 ESC。
        if data.get("black_screen_hint") in (None, "", _OLD_HINT):
            data["black_screen_hint"] = DEFAULTS["black_screen_hint"]
        data.setdefault("wake_on_input", True)
        data.setdefault("wake_rearm_seconds", 0.0)

    if version < 4:
        # v4 加入上班鼓励器。上班时间默认继承定时计划——一个人设定的"运行时段"
        # 基本就是他上下班的时间，省得重新填一遍；之后在设置里随时能改。
        data.setdefault("pay_enabled", True)
        data.setdefault("pay_monthly", 0.0)
        data.setdefault("pay_month_days", 21.75)
        data.setdefault("pay_start", data.get("schedule_start", "09:00"))
        data.setdefault("pay_end", data.get("schedule_end", "18:00"))
        data.setdefault("pay_days", list(data.get("schedule_days") or DEFAULTS["pay_days"]))

    if version < 5:
        # v5 支持午休分段。默认不分段，保持老用户原有的"一口气算到下班"口径。
        data.setdefault("pay_break_enabled", False)
        data.setdefault("pay_start2", DEFAULTS["pay_start2"])
        data.setdefault("pay_end2", DEFAULTS["pay_end2"])

    if version < 6:
        # v6 把上班鼓励器从黑屏里拆成独立悬浮窗。pay_enabled 语义变为
        # "显示桌面悬浮窗"，老用户原来的勾选状态直接继承，升级后立刻能看到。
        data.setdefault("pay_widget_x", -1)
        data.setdefault("pay_widget_y", -1)

    if version < 7:
        # v7 加了"锁定位置"和"鼠标移上来才显示"，默认都关着，保持原来的手感。
        data.setdefault("pay_widget_locked", False)
        data.setdefault("pay_widget_autohide", False)

    if version < 8:
        # v8 支持"配置方案"：整套设置存成有名字的方案。老用户先给一本空册子，
        # 第一次点「新建方案」时才往里写，不强塞一个叫"默认"的。
        data.setdefault("profiles", {})
        data.setdefault("active_profile", "")

    if version < 9:
        # v9 悬浮窗外观可调。默认值就是原来的金色 + 92% 底板，升级后看不出变化。
        data.setdefault("pay_widget_hue", 36)
        data.setdefault("pay_widget_sat", 62)
        data.setdefault("pay_widget_bg_alpha", 92)
        data.setdefault("pay_widget_text_alpha", 100)

    if version < 10:
        # v10 悬浮窗可以"点得穿过去"。默认不穿透，保持原来的手感。
        data.setdefault("pay_widget_clickthrough", 0)

    data["config_version"] = CONFIG_VERSION
    return data


class Config(dict):
    """行为类似 dict 的配置对象，缺失键自动回退到默认值。"""

    def __init__(self, data: dict | None = None):
        super().__init__(copy.deepcopy(DEFAULTS))
        if data:
            for k, v in data.items():
                if k in DEFAULTS:
                    # 类型对齐，避免配置被手改坏
                    try:
                        self[k] = type(DEFAULTS[k])(v)
                    except (TypeError, ValueError):
                        pass

    # -------------------------------------------------- 持久化
    @classmethod
    def load(cls) -> "Config":
        path = app_root() / "config.json"
        if path.exists():
            try:
                raw = json.loads(path.read_text("utf-8"))
                cfg = cls(_migrate(raw))
                if cfg["config_version"] < CONFIG_VERSION:
                    cfg["config_version"] = CONFIG_VERSION
                return cfg
            except Exception:
                pass
        return cls()

    def save(self) -> Path:
        path = app_root() / "config.json"
        try:
            path.write_text(
                json.dumps(dict(self), ensure_ascii=False, indent=2), "utf-8"
            )
        except Exception:
            pass
        return path

    def actions(self) -> list[str]:
        """当前启用的动作列表。"""
        out = []
        if self["action_black_screen"]:
            out.append("black_screen")
        if self["action_switch_window"]:
            out.append("switch_window")
        if self["action_show_desktop"]:
            out.append("show_desktop")
        if self["action_lock"]:
            out.append("lock")
        if self["action_monitor_off"]:
            out.append("monitor_off")
        return out

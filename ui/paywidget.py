# -*- coding: utf-8 -*-
"""上班鼓励器 · 桌面悬浮窗。

从黑屏遮罩里抽出来的独立小窗：无边框、置顶、可拖动、不抢焦点，
90ms 刷一次，金额精确到厘，看着像水表一样往上爬。

交互：
  拖动      左键按住任意位置拖走，松手记住位置（锁定位后拖不动）
  双击      打开设置
  右上角 ×  关掉悬浮窗（等于关掉开关）
  右键      菜单：锁定位置 / 鼠标移上来才显示 / 打开设置 / 隐藏悬浮窗 / 退出程序

两个可选行为：
  锁定位置       拖不动，防止手一滑把窗口蹭走
  鼠标移上来才显示  平时整窗透明（opacity 0），鼠标进入范围才淡入，离开再淡出
"""
from __future__ import annotations

import ctypes
import os

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from core.payroll import countdown_text, money, money_rate, rate_line

# 配色基准：深底 + 强调色。强调色不再是写死的金色，而是按色相/饱和度现算，
# 所以右上角那个"外观"滑杆一动，整窗跟着变。下面是没上色时的中性灰。
LABEL = QColor(122, 128, 136)
FAINT = QColor(74, 79, 86)
TEXT = QColor(150, 156, 164)
ACCENT_V = 222        # 强调色的明度（0–255）。压到这儿既亮又不刺眼
BG_V = 26             # 底板明度：已经黑得差不多，只剩一点同色相的余温
TRACK_V = 44          # 进度条底槽

MARGIN = 16
CLOSE_W = 18
PAY_TAB = 3           # 设置窗口里「上班鼓励器」是第 4 个标签（0 起算）

# 点击穿透相关的 Win32 常量。整窗穿透只有一条路：扩展样式 WS_EX_TRANSPARENT，
# 它是"全有或全无"的，没有"这块区域穿透、那块不穿透"的官方做法。
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
VK_CONTROL = 0x11
KEY_POLL_MS = 60      # 按住 Ctrl 的检测频率：60ms 足够跟手，又几乎不耗 CPU


def _user32():
    """设过 argtypes 的 user32。不设的话 64 位下 HWND 会被截成 32 位。"""
    u = ctypes.windll.user32
    u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.GetWindowLongW.restype = ctypes.c_long
    u.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    u.SetWindowLongW.restype = ctypes.c_long
    u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_uint]
    u.SetWindowPos.restype = ctypes.c_bool
    u.GetAsyncKeyState.argtypes = [ctypes.c_int]
    u.GetAsyncKeyState.restype = ctypes.c_short
    return u
FADE_MS = 130         # 淡入淡出时长
HOVER_POLL_MS = 110   # 自动隐藏时的光标轮询间隔
PEEK_MS = 1400        # 刚打开"移上来才显示"时，先亮一下让人知道它在哪


class PayWidget(QWidget):
    """桌面上的小工资板。"""

    WIDTH = 292
    HEIGHT = 134
    TICK_MS = 90          # 约 11fps：一行三位小数，看着是连续在走

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cfg = app.cfg

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        # 半透明圆角要这个属性；ShowWithoutActivating 让它弹出时不抢焦点
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setToolTip("上班鼓励器 · 拖动可移动，双击打开设置，右键有菜单")

        self._snap = None
        self._dragging = False
        self._drag_from = QPoint()
        self._hover = False
        self._menu_open = False
        self._peeking = False
        self._first_show = True
        self._click_through = False
        self._fade_target = 1.0        # 当前这一趟淡入淡出的目标值
        self._forced_prev = False      # 上一拍的"强制穿透"状态，用于只在变化时重绘

        # 淡入淡出：直接动 windowOpacity，比整窗重绘便宜
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(FADE_MS)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.finished.connect(self._on_fade_done)
        self._fade_to_zero = False

        # 按住 Ctrl 才穿透时，得盯着键盘 —— 穿透之后窗口收不到任何鼠标/键盘事件，
        # 没法靠信号知道"Ctrl 松了"，只能轮询全局键状态。
        self._key_timer = QTimer(self)
        self._key_timer.setInterval(KEY_POLL_MS)
        self._key_timer.timeout.connect(self._poll_modifier)

        # 光标轮询：窗口完全透明时是鼠标穿透的，收不到 enterEvent，
        # 只能自己盯着光标位置判断"要不要浮出来"。
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(HOVER_POLL_MS)
        self._hover_timer.timeout.connect(self._poll_cursor)

        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------ 显示/隐藏
    def apply_config(self) -> None:
        """按 cfg 决定显示还是隐藏。设置页开关、托盘菜单都走这里。"""
        if bool(self.cfg["pay_enabled"]):
            self.reveal()
        else:
            self.conceal()

    def reveal(self) -> None:
        self._restore_position()
        self._tick()
        self.show()
        self.raise_()
        self._timer.start()
        # 必须等窗口真的可见之后再套样式：apply_style 里要根据"可见"来决定
        # 要不要开始轮询光标（透明穿透之后收不到 enterEvent，全靠这个轮询）
        self.apply_style()
        # 刚显示时鼠标可能正好压在它身上，收不到 enterEvent，补一次判断
        QTimer.singleShot(0, self._sync_opacity)
        # 开着"移上来才显示"时，开机第一次总得亮一下，否则人以为它没启动
        if self._first_show:
            self._first_show = False
            if bool(self.cfg["pay_widget_autohide"]):
                self.peek()

    def conceal(self) -> None:
        self._timer.stop()
        self._hover_timer.stop()
        self._key_timer.stop()
        self.hide()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 兜底：reveal、以及窗口重新露头都会走到这里。只要窗口还在，
        # 就把"该盯光标"和"该盯 Ctrl"这两个轮询重新拉起来。
        QTimer.singleShot(0, self._sync_poll)
        QTimer.singleShot(0, self._sync_key_poll)
        QTimer.singleShot(0, self._refresh_click_through)

    # ------------------------------------------------------------ 外观/行为
    def apply_style(self) -> None:
        """把"锁定位置""移上来才显示"两个开关落到窗口上。设置页和右键菜单共用。"""
        locked = bool(self.cfg["pay_widget_locked"])
        self.setCursor(Qt.ArrowCursor if locked else Qt.OpenHandCursor)
        self.setToolTip(
            "上班鼓励器 · 位置已锁定（右键可解锁），双击打开设置"
            if locked
            else "上班鼓励器 · 拖动可移动，双击打开设置，右键有菜单"
        )
        self.update()

        self._sync_poll()
        self._sync_key_poll()
        self._sync_opacity()

    def _auto_enabled(self) -> bool:
        """用户有没有勾"平时完全透明"。跟当前这一瞬间该不该透明无关。"""
        return bool(self.cfg["pay_widget_autohide"])

    def _wants_autohide(self) -> bool:
        """此刻是否该处于透明待机。peek 那一秒半里不算 —— 得亮给人看。"""
        return self._auto_enabled() and not self._peeking

    def _sync_poll(self) -> None:
        """光标轮询只在「窗口可见 + 开着透明待机」时跑。

        这里必须看 _auto_enabled()，不能看 _wants_autohide()：peek 的一秒半里
        _wants_autohide() 是 False，定时器一到点就把自己停了，peek 结束后再没有
        任何东西去唤醒窗口 —— 表现就是"透明之后鼠标扫过去一点反应都没有"。
        """
        if self.isVisible() and self._auto_enabled():
            if not self._hover_timer.isActive():
                self._hover_timer.start()
        else:
            self._hover_timer.stop()

    def _cursor_inside(self) -> bool:
        try:
            return self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        except Exception:
            return False

    def _poll_cursor(self) -> None:
        """自动隐藏开着时才跑：光标进范围就浮出来，离开就淡下去。"""
        if not self.isVisible() or not self._auto_enabled():
            self._hover_timer.stop()
            self._refresh_click_through()
            return
        inside = self._cursor_inside()
        if inside != self._hover:
            self._hover = inside
            self.update()          # 边框亮度跟着变
        self._sync_opacity()

    # ------------------------------------------------------------ 点击穿透
    def _penetrate_mode(self) -> int:
        """0 不穿透 / 1 按住 Ctrl 才穿透 / 2 始终穿透。"""
        try:
            return max(0, min(2, int(self.cfg["pay_widget_clickthrough"])))
        except (TypeError, ValueError, KeyError):
            return 0

    def _modifier_down(self) -> bool:
        """Ctrl 此刻是不是按着。全局读，跟窗口有没有焦点无关。"""
        if os.name != "nt":
            return False
        try:
            return bool(_user32().GetAsyncKeyState(VK_CONTROL) & 0x8000)
        except Exception:
            return False

    def _forced_penetrate(self) -> bool:
        """这一瞬间是不是"无论如何都要让点击穿过去"，跟透明不透明无关。"""
        mode = self._penetrate_mode()
        if mode == 2:
            return True
        if mode == 1:
            return self._modifier_down()
        return False

    def _refresh_click_through(self) -> None:
        """穿透与否，全在这里收口，别处不要再直接调 _set_click_through。"""
        if not self.isVisible():
            return
        if self._forced_penetrate():
            self._set_click_through(True)
            return
        if not self._wants_autohide():
            self._set_click_through(False)
            return
        # 透明待机：必须真的淡到底、而且这一趟就是往透明走，才让点击穿过去。
        # 否则刚从 0 开始淡入时几个像素的不透明度也满足 <=0.02，
        # 会出现"窗口正浮出来却点不着"的一瞬间。
        self._set_click_through(
            self._fade_target <= 0.0 and self.windowOpacity() <= 0.02
        )

    def _sync_key_poll(self) -> None:
        """只有"按住 Ctrl 才穿透"需要盯键盘，其他模式让定时器歇着。"""
        need = self.isVisible() and self._penetrate_mode() == 1
        if need and not self._key_timer.isActive():
            self._key_timer.start()
        elif not need and self._key_timer.isActive():
            self._key_timer.stop()

    def _poll_modifier(self) -> None:
        if not self.isVisible():
            self._key_timer.stop()
            return
        forced = self._forced_penetrate()
        if forced != self._forced_prev:
            self._forced_prev = forced
            self.update()              # 虚线框提示跟着变
            self._refresh_click_through()

    def _set_click_through(self, on: bool) -> None:
        """让点击穿过窗口，落到下面的桌面 / 别的程序上。

        这里是直接改窗口的扩展样式（WS_EX_TRANSPARENT），不是 setWindowFlag ——
        Qt 换窗口标志时会重建 HWND，按住 Ctrl 时来回切会闪、还可能丢位置。
        """
        if self._click_through == on:
            return
        self._click_through = on
        self.setAttribute(Qt.WA_TransparentForMouseEvents, on)
        if on and self._dragging:
            # 拖到一半按下 Ctrl（或改成始终穿透）：鼠标事件会当场断掉，
            # release 永远收不到，窗口就会卡在"拖动中"跟着鼠标走。这里收尾。
            self._dragging = False
            self._remember_position()
            self.setCursor(
                Qt.ArrowCursor if self.cfg["pay_widget_locked"] else Qt.OpenHandCursor
            )
        if os.name != "nt":
            self.setWindowFlag(Qt.WindowTransparentForInput, on)
            return
        try:
            u = _user32()
            hwnd = ctypes.c_void_p(int(self.winId()))
            ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ex = (ex | WS_EX_TRANSPARENT) if on else (ex & ~WS_EX_TRANSPARENT)
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
            # 改完扩展样式要让系统重算窗口框架，不然偶尔要等下一次重绘才生效
            u.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                           SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                           | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        except Exception:
            self.setWindowFlag(Qt.WindowTransparentForInput, on)

    def _sync_opacity(self) -> None:
        """按当前光标位置决定该透明还是该显示。"""
        if not self.isVisible():
            return
        if not self._wants_autohide():
            self._fade_to(1.0)
            self._refresh_click_through()
            return
        shown = self._menu_open or self._hover or self._cursor_inside()
        self._fade_to(1.0 if shown else 0.0)
        self._refresh_click_through()

    def _fade_to(self, target: float) -> None:
        self._fade.stop()
        current = self.windowOpacity()
        self._fade_to_zero = target <= 0.0
        self._fade_target = target
        if abs(current - target) < 0.02:
            self.setWindowOpacity(target)
            self._refresh_click_through()
            return
        self._fade.setStartValue(current)
        self._fade.setEndValue(target)
        self._fade.start()

    def _on_fade_done(self) -> None:
        # 淡到全透明之后才允许穿透，免得"正往外走又想点回来"时点空
        self._refresh_click_through()

    def peek(self) -> None:
        """刚勾上"移上来才显示"时先亮一下：不然窗口直接消失，人都不知道它在哪。"""
        if not self.isVisible():
            return
        self._peeking = True
        self.setWindowOpacity(1.0)
        QTimer.singleShot(PEEK_MS, self._end_peek)

    def _end_peek(self) -> None:
        self._peeking = False
        self._sync_poll()
        self._sync_opacity()

    def _restore_position(self) -> None:
        """优先用上次记住的位置；没有或不合法就贴右下角。"""
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        try:
            x = int(self.cfg["pay_widget_x"])
            y = int(self.cfg["pay_widget_y"])
        except (TypeError, ValueError):
            x = y = -1

        if x < 0 or y < 0:
            x = area.right() - self.WIDTH - 24
            y = area.bottom() - self.HEIGHT - 24
        else:
            # 换过显示器、改过分辨率之后，旧坐标可能落在屏幕外，夹回来
            x = max(area.left(), min(x, area.right() - self.WIDTH))
            y = max(area.top(), min(y, area.bottom() - self.HEIGHT))
        self.move(x, y)

    def _remember_position(self) -> None:
        pos = self.pos()
        self.cfg["pay_widget_x"] = int(pos.x())
        self.cfg["pay_widget_y"] = int(pos.y())
        self.cfg.save()

    # ------------------------------------------------------------ 数据
    def _tick(self) -> None:
        snap = None
        try:
            snap = self.app.pay_snapshot()
        except Exception:
            snap = None
        self._snap = snap if (snap is not None and snap.ok) else None
        self.update()

    # ------------------------------------------------------------ 配色
    @staticmethod
    def _fraction(cfg, key: str, default: float = 100.0) -> float:
        """把配置里的 0–100 读成 0–1，坏值一律退回默认。"""
        try:
            return max(0.0, min(100.0, float(cfg[key]))) / 100.0
        except (TypeError, ValueError, KeyError):
            return default / 100.0

    def _hue(self) -> int:
        try:
            return int(self.cfg["pay_widget_hue"]) % 360
        except (TypeError, ValueError, KeyError):
            return 36

    def _sat(self) -> int:
        """0–255 的饱和度（已经乘过用户那个百分比）。"""
        return int(round(255 * self._fraction(self.cfg, "pay_widget_sat", 62)))

    def accent(self, alpha: int = 255, value: int = ACCENT_V) -> QColor:
        """强调色：金额、进度条、边框、✕。色相一变整窗气氛就变。"""
        return QColor.fromHsv(self._hue(), self._sat(), value,
                              max(0, min(255, int(alpha))))

    def bg_color(self) -> QColor:
        """底板：同色相的极暗版本，再乘上用户设的不透明度。

        注意这里是 HSV 的明度压到 26，不是把强调色直接调透明 ——
        半透明的亮色底会把桌面透上来，数字反而看不清。
        """
        alpha = int(round(255 * self._fraction(self.cfg, "pay_widget_bg_alpha", 92)))
        return QColor.fromHsv(self._hue(), int(self._sat() * 0.30), BG_V, alpha)

    def track_color(self) -> QColor:
        return QColor.fromHsv(self._hue(), int(self._sat() * 0.35), TRACK_V)

    def _text_alpha(self) -> int:
        return int(round(255 * self._fraction(self.cfg, "pay_widget_text_alpha", 100)))

    def _t(self, color: QColor) -> QColor:
        """文字色带上"文字不透明度"，背景调淡时一并变淡，不至于黑字压灰底。"""
        c = QColor(color)
        c.setAlpha(self._text_alpha())
        return c

    def text_accent(self) -> QColor:
        """当文字用的强调色（金额、✕）。跟着"文字不透明度"一起走。"""
        return self.accent(self._text_alpha())

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        body = self.rect().adjusted(1, 1, -1, -1)
        p.setPen(self.accent(150 if self._hover else 42))
        p.setBrush(self.bg_color())
        p.drawRoundedRect(body, 13, 13)

        if self._snap is None:
            self._paint_empty(p)
        else:
            self._paint_pay(p)

        # 右上角的小 ×：压得很暗，鼠标移上来才亮
        p.setPen(self.text_accent() if self._hover else self._t(FAINT))
        p.setFont(QFont("Microsoft YaHei UI", 10))
        p.drawText(self._close_rect(), Qt.AlignCenter, "✕")

        # 按住 Ctrl 穿透的那几秒，边框换成虚线：不然窗口看着一样、点下去却穿过去，
        # 人会以为程序坏了。"始终穿透"是常态，就不加这个框，免得一直闪。
        if self._penetrate_mode() == 1 and self._forced_penetrate():
            pen = QPen(self.accent(165))
            pen.setStyle(Qt.CustomDashLine)
            pen.setDashPattern([3.0, 4.0])
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(body.adjusted(3, 3, -3, -3), 10, 10)

    def _close_rect(self) -> QRect:
        return QRect(self.WIDTH - CLOSE_W - 12, 9, CLOSE_W, CLOSE_W)

    def _paint_empty(self, p: QPainter) -> None:
        p.setPen(self._t(LABEL))
        p.setFont(QFont("Microsoft YaHei UI", 11))
        p.drawText(
            QRect(MARGIN, 40, self.WIDTH - MARGIN * 2, 24), Qt.AlignLeft, "上班鼓励器"
        )
        p.setPen(self.text_accent())
        p.setFont(QFont("Microsoft YaHei UI", 12))
        p.drawText(
            QRect(MARGIN, 68, self.WIDTH - MARGIN * 2, 24),
            Qt.AlignLeft, "还没填月薪 · 点我打开设置",
        )

    def _paint_pay(self, p: QPainter) -> None:
        snap = self._snap
        inner = self.WIDTH - MARGIN * 2

        # 标题：字间距拉开一点，显得像仪表盘
        p.setPen(self._t(LABEL))
        f = QFont("Microsoft YaHei UI", 9)
        f.setLetterSpacing(QFont.PercentageSpacing, 118)
        p.setFont(f)
        p.drawText(QRect(MARGIN, 12, inner, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   "今 日 已 赚")

        # 金额：整块屏幕上最亮的东西，到厘
        p.setPen(self.text_accent())
        f = QFont("Microsoft YaHei UI", 25)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.drawText(QRect(MARGIN - 2, 28, inner + 4, 40),
                   Qt.AlignLeft | Qt.AlignVCenter, money(snap.earned, 3))

        # 费率行
        p.setPen(self._t(FAINT))
        p.setFont(QFont("Microsoft YaHei UI", 8))
        p.drawText(QRect(MARGIN, 72, inner, 15), Qt.AlignLeft | Qt.AlignVCenter,
                   rate_line(snap))

        # 进度条
        bar = QRect(MARGIN, 94, inner, 3)
        p.setPen(Qt.NoPen)
        p.setBrush(self.track_color())
        p.drawRoundedRect(bar, 1.5, 1.5)
        done = int(inner * max(0.0, min(1.0, snap.progress)))
        if done > 0:
            p.setBrush(self.accent())
            p.drawRoundedRect(QRect(MARGIN, 94, done, 3), 1.5, 1.5)

        # 倒计时 / 状态
        p.setPen(self._t(TEXT))
        p.setFont(QFont("Microsoft YaHei UI", 10))
        p.drawText(QRect(MARGIN, 103, inner, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   countdown_text(snap))

    # ------------------------------------------------------------ 鼠标
    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        self._sync_opacity()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        # 右键菜单弹着的时候鼠标会先离开窗口，这时候别急着淡出
        if not self._menu_open:
            self._sync_opacity()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            if self._close_rect().contains(event.position().toPoint()):
                self._close()
                return
            if bool(self.cfg["pay_widget_locked"]):
                # 锁定了就不接拖动，点击照旧吃掉，别漏到桌面上去
                event.accept()
                return
            self._dragging = True
            self._drag_from = event.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_from)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            self._dragging = False
            self.setCursor(
                Qt.ArrowCursor if self.cfg["pay_widget_locked"] else Qt.OpenHandCursor
            )
            self._remember_position()
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.app.open_settings()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)

        act_lock = menu.addAction("锁定位置")
        act_lock.setCheckable(True)
        act_lock.setChecked(bool(self.cfg["pay_widget_locked"]))

        act_auto = menu.addAction("鼠标移上来才显示")
        act_auto.setCheckable(True)
        act_auto.setChecked(bool(self.cfg["pay_widget_autohide"]))

        sub = menu.addMenu("点击穿透")
        self._ct_acts = []
        for label, mode in (
            ("不穿透（正常点它）", 0),
            ("按住 Ctrl 时穿透", 1),
            ("始终穿透（看不见鼠标也不吃点击）", 2),
        ):
            a = sub.addAction(label)
            a.setCheckable(True)
            a.setChecked(self._penetrate_mode() == mode)
            a.setData(mode)
            self._ct_acts.append(a)

        menu.addSeparator()
        act_blackout = menu.addAction("关闭检测并黑屏")
        act_skin = menu.addAction("调整外观（颜色 / 透明度）…")
        act_settings = menu.addAction("打开设置")
        act_hide = menu.addAction("隐藏悬浮窗")
        menu.addSeparator()
        act_quit = menu.addAction("退出程序")

        self._menu_open = True
        picked = menu.exec(event.globalPos())
        self._menu_open = False

        if picked == act_lock:
            self.cfg["pay_widget_locked"] = not bool(self.cfg["pay_widget_locked"])
            self.cfg.save()
            self.apply_style()
            self._sync_settings()
        elif picked == act_auto:
            self.cfg["pay_widget_autohide"] = not bool(self.cfg["pay_widget_autohide"])
            self.cfg.save()
            self.apply_style()
            if self.cfg["pay_widget_autohide"]:
                self.peek()          # 先亮一下，告诉人"我还在这"
            self._sync_settings()
        elif picked == act_blackout:
            self.app.pause_and_blackout()
        elif picked == act_skin:
            self.app.open_settings(PAY_TAB)
        elif picked in getattr(self, "_ct_acts", []):
            self.set_penetrate_mode(int(picked.data()) or 0)
        elif picked == act_settings:
            self.app.open_settings()
        elif picked == act_hide:
            self._close()
        elif picked == act_quit:
            self.app.quit()
        else:
            self._sync_opacity()

    def set_penetrate_mode(self, mode: int) -> None:
        """切换点击穿透。设置页、托盘菜单、右键菜单三处都走这里。"""
        mode = max(0, min(2, int(mode)))
        self.cfg["pay_widget_clickthrough"] = mode
        self.cfg.save()
        self._sync_key_poll()
        self._refresh_click_through()
        self.update()
        self._sync_settings()
        if mode == 2:
            # 一旦"始终穿透"，这个窗口就再也点不开了，得告诉人从哪改回来
            try:
                self.app.settings.notify(
                    "已设为「始终穿透」：悬浮窗不再吃点击，"
                    "要改回来请到托盘图标右键菜单"
                )
            except Exception:
                pass

    def _close(self) -> None:
        """关掉悬浮窗 = 关掉开关，设置页里的勾也要跟着落下来。"""
        self.cfg["pay_enabled"] = False
        self.cfg.save()
        self.conceal()
        self._sync_settings()

    def _sync_settings(self) -> None:
        try:
            self.app.settings.sync_pay_switch()
        except Exception:
            pass

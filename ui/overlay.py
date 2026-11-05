# -*- coding: utf-8 -*-
"""全屏黑屏遮罩（Qt 实现，覆盖全部显示器，淡入淡出）。"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPalette
from PySide6.QtWidgets import QWidget

from core.actions import virtual_screen_rect
from core.payroll import countdown_text, money, money_rate, rate_line
from core.wake import WakeWatcher


class ScreenOverlay(QWidget):
    """覆盖所有显示器的黑色置顶窗口。

    解除黑屏的两条路：
      1. 任意键 / 鼠标移动（全局检测，不依赖窗口焦点）；
      2. ESC（同样有效，留作习惯用法）。
    """

    dismissed = Signal()

    # 键鼠轮询间隔。60ms 足够跟手，又不会白烧 CPU。
    POLL_MS = 60

    # 工资板的刷新间隔。金额按秒折算，1 秒一刷在屏幕上就是"一秒跳一格"；
    # 90ms 一刷（约 11fps）配上厘位小数，看到的才是水表一样连续往上爬。
    PAY_MS = 90

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setCursor(Qt.BlankCursor)
        self.setFocusPolicy(Qt.StrongFocus)

        # 背景固定黑：下面做局部重绘，只刷工资板那一块，未重绘的区域靠
        # 后备缓冲留着就行，但得保证没有一处会露白。
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor(0, 0, 0))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        self._title = ""
        self._hint = ""
        self._closing = False
        self._wake_on_input = True

        # 上班鼓励器：_pay_provider 每次调用返回一个 PaySnapshot 或 None
        self._pay_provider = None
        self._pay = None
        self._pay_timer = QTimer(self)
        self._pay_timer.setInterval(self.PAY_MS)
        self._pay_timer.timeout.connect(self._tick_pay)

        self._wake = WakeWatcher()
        self._wake_timer = QTimer(self)
        self._wake_timer.setInterval(self.POLL_MS)
        self._wake_timer.timeout.connect(self._poll_wake)

        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_anim_done)

        # 让 ESC 在所有屏幕上都能生效
        self.setAttribute(Qt.WA_DeleteOnClose, False)

    # ------------------------------------------------------------ 显示/隐藏
    def show_overlay(
        self, text: str = "", hint: str = "", wake_on_input: bool = True,
        pay_provider=None,
    ) -> None:
        self._title = text
        self._hint = hint
        self._closing = False
        self._wake_on_input = bool(wake_on_input)
        self._pay_provider = pay_provider

        x, y, w, h = virtual_screen_rect()
        self.setGeometry(x, y, w, h)

        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

        # 基线必须在窗口真正出现之后再采，否则窗口弹出引起的焦点变化
        # 会被当成"用户输入"，遮罩自己把自己关掉。
        self._wake.arm()
        if self._wake_on_input:
            self._wake_timer.start()
        else:
            self._wake_timer.stop()

        # 工资板立刻算一次并开始高频刷新：黑屏上那串钱要一秒一秒往上爬
        self._tick_pay()
        if self._pay is not None:
            self._pay_timer.start()
        else:
            self._pay_timer.stop()

        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def hide_overlay(self, animate: bool = True) -> None:
        self._stop_watch()
        if not self.isVisible():
            return
        if not animate:
            self._anim.stop()
            self.hide()
            return
        self._closing = True
        self._anim.stop()
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _stop_watch(self) -> None:
        self._wake_timer.stop()
        self._wake.disarm()
        self._pay_timer.stop()

    # ------------------------------------------------------------ 上班鼓励器
    def _board_rect(self) -> QRect:
        """工资板占据的区域。局部重绘只刷这一块，不必每 90ms 重画整个屏幕。"""
        cy = self.height() // 2
        return QRect(0, cy - 225, self.width(), 285)

    def _tick_pay(self) -> None:
        snap = None
        if self._pay_provider is not None:
            try:
                snap = self._pay_provider()
            except Exception:
                snap = None
        self._pay = snap if (snap is not None and snap.ok) else None
        if self.isVisible():
            self.update(self._board_rect())

    # ------------------------------------------------------------ 唤醒
    def _poll_wake(self) -> None:
        # 遮罩没显示的时候不该有任何动作（防止淡出动画期间误触发）
        if not self.isVisible():
            self._stop_watch()
            return
        if self._wake.poll():
            self._dismiss()

    def _dismiss(self) -> None:
        if self._closing or not self.isVisible():
            return
        self.hide_overlay()
        self.dismissed.emit()

    # ------------------------------------------------------------ 事件
    def _on_anim_done(self) -> None:
        if self._closing:
            self._closing = False
            self.hide()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # 有焦点时直接在这里响应，比等轮询快一拍；没焦点时靠 _wake 兜底。
        if event.key() == Qt.Key_Escape or self._wake_on_input:
            self._dismiss()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # 一律吃掉点击，避免点到下面的窗口；开着键鼠唤醒时这次点击就是唤醒。
        if self._wake_on_input:
            self._dismiss()
        event.accept()

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        # 只刷需要重绘的那块：全屏 2560×1600 每 90ms 填一遍太浪费，
        # 未重绘的区域留在后备缓冲里照样是黑的。
        p.fillRect(event.rect(), QColor(0, 0, 0))

        if self._pay is not None:
            self._paint_pay(p)
            self._paint_footer(p)
            return

        if not self._title:
            return

        p.setPen(QColor(92, 96, 104))
        f = QFont("Microsoft YaHei UI", 26)
        f.setWeight(QFont.Light)
        p.setFont(f)
        rect = self.rect().adjusted(0, -40, 0, -40)
        p.drawText(rect, Qt.AlignCenter, self._title)

        if self._hint:
            p.setPen(QColor(62, 66, 72))
            p.setFont(QFont("Microsoft YaHei UI", 11))
            hint_rect = self.rect().adjusted(0, 90, 0, 90)
            p.drawText(hint_rect, Qt.AlignCenter, self._hint)

    def _paint_pay(self, p: QPainter) -> None:
        """黑屏正中的工资板：今日已赚 / 时薪 / 进度条 / 距下班。"""
        snap = self._pay
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2

        # 标题小字
        p.setPen(QColor(96, 101, 108))
        f = QFont("Microsoft YaHei UI", 12)
        f.setLetterSpacing(QFont.PercentageSpacing, 112)
        p.setFont(f)
        p.drawText(QRect(0, cy - 210, w, 26), Qt.AlignCenter, "今 日 已 赚")

        # 金额：整个屏幕最亮的东西，精确到厘，看着它一秒一秒往上走
        p.setPen(QColor(214, 160, 78))
        f = QFont("Microsoft YaHei UI", 46)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.drawText(QRect(0, cy - 175, w, 92), Qt.AlignCenter, money(snap.earned, 3))

        # 时薪 / 每分钟
        p.setPen(QColor(88, 93, 100))
        p.setFont(QFont("Microsoft YaHei UI", 11))
        p.drawText(QRect(0, cy - 70, w, 24), Qt.AlignCenter, rate_line(snap))

        # 今日进度条
        bar_w, bar_h = 420, 4
        x0, y0 = cx - bar_w // 2, cy - 20
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(28, 30, 34))
        p.drawRoundedRect(QRect(x0, y0, bar_w, bar_h), 2, 2)
        p.setBrush(QColor(214, 160, 78))
        done = int(bar_w * max(0.0, min(1.0, snap.progress)))
        if done > 0:
            p.drawRoundedRect(QRect(x0, y0, done, bar_h), 2, 2)

        # 距下班
        p.setPen(QColor(132, 138, 146))
        p.setFont(QFont("Microsoft YaHei UI", 15))
        p.drawText(QRect(0, cy + 0, w, 40), Qt.AlignCenter, countdown_text(snap))

    def _paint_footer(self, p: QPainter) -> None:
        """黑屏底部的原提示语，压得很暗，不抢工资的视线。"""
        w, h = self.width(), self.height()
        if self._title:
            p.setPen(QColor(46, 49, 54))
            p.setFont(QFont("Microsoft YaHei UI", 11))
            p.drawText(QRect(0, h - 96, w, 24), Qt.AlignCenter, self._title)
        if self._hint:
            p.setPen(QColor(38, 40, 45))
            p.setFont(QFont("Microsoft YaHei UI", 10))
            p.drawText(QRect(0, h - 68, w, 22), Qt.AlignCenter, self._hint)


def primary_screen_size() -> tuple[int, int]:
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return 1920, 1080
    g = screen.geometry()
    return g.width(), g.height()

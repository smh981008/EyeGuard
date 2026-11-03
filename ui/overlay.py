# -*- coding: utf-8 -*-
"""全屏黑屏遮罩（Qt 实现，覆盖全部显示器，淡入淡出）。"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from core.actions import virtual_screen_rect
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

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setCursor(Qt.BlankCursor)
        self.setFocusPolicy(Qt.StrongFocus)

        self._title = ""
        self._hint = ""
        self._closing = False
        self._wake_on_input = True

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
        self, text: str = "", hint: str = "", wake_on_input: bool = True
    ) -> None:
        self._title = text
        self._hint = hint
        self._closing = False
        self._wake_on_input = bool(wake_on_input)

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
        p.fillRect(self.rect(), QColor(0, 0, 0))

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


def primary_screen_size() -> tuple[int, int]:
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return 1920, 1080
    g = screen.geometry()
    return g.width(), g.height()

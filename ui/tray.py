# -*- coding: utf-8 -*-
"""系统托盘图标（Qt 实现）。"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

COLOR_PRESENT = (18, 183, 106)
COLOR_AWAY = (240, 68, 56)
COLOR_PAUSED = (154, 164, 178)


def make_icon(color: tuple[int, int, int], closed: bool = False) -> QIcon:
    """画一个眼睛图标。closed=True 画闭眼，用于"已离开"状态。"""
    size = 128
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    p.setPen(Qt.NoPen)
    p.setBrush(QColor(*color))
    p.drawEllipse(4, 4, size - 8, size - 8)

    white = QColor(255, 255, 255)
    if closed:
        pen = QPen(white, 9)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(30, 34, 68, 56), 20 * 16, 140 * 16)
        p.drawLine(44, 76, 52, 88)
        p.drawLine(64, 84, 64, 96)
        p.drawLine(84, 76, 76, 88)
    else:
        p.setPen(Qt.NoPen)
        p.setBrush(white)
        p.drawEllipse(QRectF(24, 40, 80, 48))
        p.setBrush(QColor(*color))
        p.drawEllipse(QRectF(50, 46, 28, 36))
        p.setBrush(QColor(20, 20, 20))
        p.drawEllipse(QRectF(58, 52, 12, 24))
        p.setBrush(white)
        p.drawEllipse(QRectF(61, 55, 5, 7))

    p.end()
    return QIcon(pm)


class Tray(QSystemTrayIcon):
    def __init__(self, app):
        super().__init__(make_icon(COLOR_PAUSED))
        self.app = app
        self.setToolTip("视线守卫 · 已暂停")

        menu = QMenu()
        self.act_settings = QAction("打开设置", menu)
        self.act_settings.triggered.connect(app.open_settings)
        menu.addAction(self.act_settings)
        menu.setDefaultAction(self.act_settings)

        self.act_enable = QAction("启用检测", menu)
        self.act_enable.setCheckable(True)
        self.act_enable.triggered.connect(app.toggle_enabled)
        menu.addAction(self.act_enable)

        self.act_pay = QAction("上班鼓励器悬浮窗", menu)
        self.act_pay.setCheckable(True)
        self.act_pay.triggered.connect(app.toggle_pay_widget)
        menu.addAction(self.act_pay)

        # 悬浮窗设成"始终穿透"之后就完全点不开了，这里是唯一的改回来的入口
        self.menu_pierce = menu.addMenu("悬浮窗点击穿透")
        self.pierce_acts = []
        for label, mode in (
            ("不穿透", 0),
            ("按住 Ctrl 时穿透", 1),
            ("始终穿透（只能从这里改回来）", 2),
        ):
            act = self.menu_pierce.addAction(label)
            act.setCheckable(True)
            act.setData(mode)
            act.triggered.connect(
                lambda _=False, m=mode: app.set_penetrate_mode(int(m))
            )
            self.pierce_acts.append(act)

        menu.addSeparator()

        self.act_cal = QAction("校准基准姿态", menu)
        self.act_cal.triggered.connect(app.calibrate)
        menu.addAction(self.act_cal)

        self.act_test = QAction("测试触发一次", menu)
        self.act_test.triggered.connect(app.test_trigger)
        menu.addAction(self.act_test)

        self.act_hide = QAction("关闭黑屏遮罩", menu)
        self.act_hide.triggered.connect(app.hide_black_screen)
        menu.addAction(self.act_hide)

        menu.addSeparator()

        self.act_auto = QAction("开机自动启动", menu)
        self.act_auto.setCheckable(True)
        self.act_auto.triggered.connect(app.toggle_autostart)
        menu.addAction(self.act_auto)

        menu.addSeparator()

        self.act_quit = QAction("退出", menu)
        self.act_quit.triggered.connect(app.quit)
        menu.addAction(self.act_quit)

        menu.aboutToShow.connect(self.sync_menu)
        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)
        self.sync_menu()

    def sync_menu(self) -> None:
        """菜单里的勾选状态跟 cfg 对齐。设置页改了开关也会调过来。"""
        try:
            self.act_enable.setChecked(bool(self.app.cfg["enabled"]))
            self.act_auto.setChecked(bool(self.app.cfg["start_with_windows"]))
            self.act_pay.setChecked(bool(self.app.cfg["pay_enabled"]))
            mode = int(self.app.cfg["pay_widget_clickthrough"])
            for act in self.pierce_acts:
                act.setChecked(int(act.data()) == mode)
        except RuntimeError:
            pass

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.app.open_settings()

    def update_state(self, state: str) -> None:
        # 图标重绘有成本，状态没变就不动
        if getattr(self, "_last_state", None) == state:
            return
        self._last_state = state
        try:
            if state == "present":
                self.setIcon(make_icon(COLOR_PRESENT))
                self.setToolTip("视线守卫 · 在位")
            elif state == "away":
                self.setIcon(make_icon(COLOR_AWAY, closed=True))
                self.setToolTip("视线守卫 · 已离开")
            else:
                self.setIcon(make_icon(COLOR_PAUSED))
                self.setToolTip("视线守卫 · 已暂停")
        except Exception:
            pass

# -*- coding: utf-8 -*-
"""视线守卫 · 主程序（PySide6）。

架构：
  主线程   Qt 事件循环（设置窗口、黑屏遮罩、托盘）
  子线程   Detector 摄像头采集与判定，通过信号回到主线程
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from core.actions import is_autostart_on, set_autostart
from core.activity import idle_seconds
from core.config import APP_NAME, Config, app_root
from core.detector import STATE_AWAY, STATE_PAUSED, STATE_PRESENT, Detector
from core.schedule import describe_now, in_window
from ui.overlay import ScreenOverlay
from ui.settings import SettingsWindow
from ui.theme import QSS
from ui.tray import Tray, make_icon

SINGLE_INSTANCE_PORT = 47831


def _ensure_single_instance() -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


class Bridge(QObject):
    """把 detector 子线程的回调变成 Qt 信号，自动排队到主线程。"""

    leave = Signal(list)
    returned = Signal()
    calibrated = Signal(float, float)


class DesktopApp(QObject):
    def __init__(self, qapp: QApplication, cfg: Config):
        super().__init__()
        self.qapp = qapp
        self.cfg = cfg
        self._lock_cursor = None

        self.overlay = ScreenOverlay()
        self.overlay.dismissed.connect(self._on_overlay_dismissed)
        self._overlay_dismissed = False
        self.bridge = Bridge()
        self.bridge.leave.connect(self._on_leave)
        self.bridge.returned.connect(self._on_return)
        self.bridge.calibrated.connect(self._on_calibrated)

        self.detector = Detector(
            self.cfg,
            on_leave=lambda reasons: self.bridge.leave.emit(list(reasons)),
            on_return=lambda: self.bridge.returned.emit(),
            on_calibrated=lambda x, y: self.bridge.calibrated.emit(x, y),
        )

        self.tray = Tray(self)
        self.settings = SettingsWindow(self)

        self._state_timer = QTimer(self)
        self._state_timer.timeout.connect(self._poll_state)
        self._state_timer.start(450)

        # 黑屏看门狗：确保遮罩始终盖在最前面（见 _watch_overlay）
        self._overlay_timer = QTimer(self)
        self._overlay_timer.timeout.connect(self._watch_overlay)
        self._overlay_timer.start(1500)

        # 定时计划：每 20 秒对一次表，到了边界才切一次状态。
        # 不在每次 tick 都"纠正"，否则用户手动启停会被立刻覆盖。
        self._schedule_in: bool | None = None
        self._schedule_timer = QTimer(self)
        self._schedule_timer.timeout.connect(self._check_schedule)
        self._schedule_timer.start(20000)

    # ------------------------------------------------------------ 启动
    def start(self) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.detector.start()

        # 启动时先按计划对一次表：比如现在是晚上，计划只到 18:00，
        # 那就别先开 20 秒再关掉。
        QTimer.singleShot(600, self._check_schedule)

        first_run = not (app_root() / "config.json").exists()
        if first_run:
            QTimer.singleShot(500, self.open_settings)
            # 首次使用直接把「判定规则」页翻出来，校准按钮在那儿
            QTimer.singleShot(650, lambda: self.settings.tabs.setCurrentIndex(1))

    # ------------------------------------------------------------ 状态
    def _poll_state(self) -> None:
        if not self.cfg["enabled"]:
            self.tray.update_state("paused")
            return
        state = self.detector.snapshot().state
        self.tray.update_state(
            "present" if state == STATE_PRESENT
            else "away" if state == STATE_AWAY
            else "paused"
        )

    # ------------------------------------------------------------ 触发
    def _show_black_screen(self) -> None:
        self.overlay.show_overlay(
            str(self.cfg["black_screen_text"]),
            str(self.cfg["black_screen_hint"]),
            wake_on_input=bool(self.cfg["wake_on_input"]),
        )

    def _on_leave(self, reasons: list[str]) -> None:
        actions = self.cfg.actions()
        if not actions:
            return
        if "black_screen" in actions:
            self._overlay_dismissed = False
            self._show_black_screen()
        rest = [a for a in actions if a != "black_screen"]
        if rest:
            QTimer.singleShot(150, lambda: self._run_actions(rest))

    def _on_return(self) -> None:
        self._overlay_dismissed = False
        self.overlay.hide_overlay()

    def _on_overlay_dismissed(self) -> None:
        # 用户按键鼠或 ESC 主动关掉，这一轮就不再自动弹回来
        self._overlay_dismissed = True

    def _watch_overlay(self) -> None:
        """黑屏期间持续确认遮罩在最前面、而且没被关掉。

        Windows 上任何新出现的置顶窗口（播放器、通知横幅、游戏、远程
        桌面）都能盖住我们的遮罩，表现就是"人明明不在，屏幕却自己亮
        了"，而检测状态一切正常。所以只要状态还是"离开"，就周期性
        把它重新提到最上层。
        """
        if not self.cfg["enabled"]:
            return
        if "black_screen" not in self.cfg.actions():
            return
        if self.detector.snapshot().state != STATE_AWAY:
            return
        if self._overlay_dismissed:
            self._maybe_rearm()
            return
        if not self.overlay.isVisible():
            self._show_black_screen()
        else:
            self.overlay.raise_()

    def _maybe_rearm(self) -> None:
        """用户按键鼠解除了黑屏，之后又是长时间没有任何输入、人脸也还
        不在——说明刚才多半只是手肘/猫碰了一下键鼠，把遮罩盖回去。

        默认关闭（wake_rearm_seconds = 0）：用户按了键就是要看屏幕，不该
        在几秒后又黑下来。
        """
        try:
            wait = float(self.cfg["wake_rearm_seconds"])
        except (TypeError, ValueError):
            return
        if wait <= 0 or idle_seconds() < wait:
            return
        self._overlay_dismissed = False
        self._show_black_screen()

    def _on_calibrated(self, _x: float, _y: float) -> None:
        # 首次运行自动学习完成，或者用户手动点了校准
        self._toast("校准完成，正对屏幕已设为基准")

    @staticmethod
    def _run_actions(names: list[str]) -> None:
        from core.actions import run_actions

        try:
            run_actions(names)
        except Exception:
            pass

    # ------------------------------------------------------------ 用户操作
    def open_settings(self) -> None:
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def set_enabled(self, want: bool) -> None:
        """显式设置运行状态。定时计划用它，不能用"切换"——手动暂停过的
        状态下再取反，会把本该暂停的时段给开起来。"""
        want = bool(want)
        if bool(self.cfg["enabled"]) == want:
            return
        self.cfg["enabled"] = want
        self._overlay_dismissed = False
        if want:
            self.detector.resume()
        else:
            self.overlay.hide_overlay(animate=False)
            self.detector.pause()

    def toggle_enabled(self) -> None:
        self.set_enabled(not self.cfg["enabled"])

    # ------------------------------------------------------------ 定时计划
    def _check_schedule(self) -> None:
        """只在"进入/离开时段"这一刻动作，中间不干预用户的手动启停。"""
        if not self.cfg["schedule_enabled"]:
            self._schedule_in = None
            return

        now_in = in_window(self.cfg)
        if now_in == self._schedule_in:
            return
        self._schedule_in = now_in

        if now_in and not self.cfg["enabled"]:
            self.set_enabled(True)
            self._toast("定时计划：进入运行时段，检测已启用")
        elif not now_in and self.cfg["enabled"]:
            self.set_enabled(False)
            self._toast("定时计划：已离开运行时段，检测已暂停")

    def check_schedule_now(self) -> None:
        """设置页改了时间/星期/开关后立刻重算一次，不用等 20 秒轮询。

        故意**不**重置边界记录：只有在"跨越时段边界"或"刚打开计划"时才
        动作，这样用户在时段外手动启用检测，不会因为动了一下星期就被推翻。
        """
        self._check_schedule()

    def schedule_state_text(self) -> str:
        return describe_now(self.cfg)

    def calibrate(self) -> None:
        snap = self.detector.snapshot()
        if not self.cfg["enabled"]:
            self._toast("请先开启检测，再校准")
            return
        if not snap.face_found:
            self._toast("没检测到人脸，请正对摄像头再校准")
            return
        self.detector.calibrate()
        self._toast("正在校准，请正对屏幕保持约 2 秒不要转头")

    def test_trigger(self) -> None:
        self._on_leave(["手动测试"])
        # 测试时人脸一直在，状态机不会自动恢复，3 秒后主动收起
        QTimer.singleShot(3000, self.overlay.hide_overlay)

    def toggle_autostart(self) -> None:
        want = not bool(self.cfg["start_with_windows"])
        if getattr(sys, "frozen", False):
            exe = str(Path(sys.executable).resolve())
        else:
            exe = f'"{sys.executable}" "{app_root() / "app.py"}"'
        ok, err = set_autostart(want, exe)
        if ok:
            self.cfg["start_with_windows"] = want
            self.cfg.save()
            self._toast("已开启开机自启" if want else "已关闭开机自启")
        else:
            self._toast(f"设置失败：{err}")

    def _toast(self, msg: str) -> None:
        try:
            self.settings.msg.setText(msg)
            QTimer.singleShot(3000, lambda: self.settings.msg.setText(""))
        except Exception:
            pass
        print(msg)

    # ------------------------------------------------------------ 退出
    def quit(self) -> None:
        self.cfg.save()
        self._state_timer.stop()
        self._overlay_timer.stop()
        try:
            self.overlay.hide_overlay(animate=False)
        except Exception:
            pass
        try:
            self.detector.stop()
        except Exception:
            pass
        try:
            self.tray.hide()
        except Exception:
            pass
        try:
            self.settings.force_close()
        except Exception:
            pass
        self.qapp.quit()


def main() -> None:
    if os.name != "nt":
        print("仅支持 Windows。")
        return

    lock = _ensure_single_instance()

    qapp = QApplication(sys.argv)
    qapp.setApplicationName(APP_NAME)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setStyleSheet(QSS)
    qapp.setWindowIcon(make_icon((47, 111, 237)))

    if lock is None:
        QMessageBox.information(
            None, APP_NAME, "视线守卫已经在运行了，看右下角托盘图标。"
        )
        return

    cfg = Config.load()
    if cfg["start_with_windows"] and not is_autostart_on():
        cfg["start_with_windows"] = False

    app = DesktopApp(qapp, cfg)
    app.start()

    qapp.aboutToQuit.connect(lambda: lock.close())
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()

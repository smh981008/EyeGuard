# -*- coding: utf-8 -*-
"""全局快捷键：Ctrl + Alt + B —— 一键暂停检测并黑屏。

为什么不用 RegisterHotKey：
  它要求注册的那个线程跑一个 GetMessage 循环，而主线程已经被 Qt 占着；
  另开一条线程专门等热键，就多了一份生命周期要管（退出时得 PostThreadMessage
  把循环叫醒）。GetAsyncKeyState 是"问一句当前按没按"，Qt 定时器本来就在跑，
  顺手看一眼就够了。

代价有两个，都能接受：
  * 轮询有间隔，响应最快也就 90ms —— 这不是打字工具，够用；
  * 必须自己做"按下沿"去重，否则按住不放会每 90ms 触发一次。
"""
from __future__ import annotations

import ctypes

from PySide6.QtCore import QObject, QTimer

VK_CONTROL = 0x11
VK_MENU = 0x12          # Alt
VK_B = 0x42

HOTKEY_LABEL = "Ctrl + Alt + B"


def _user32():
    """设过 argtypes 的 user32。"""
    u = ctypes.windll.user32
    u.GetAsyncKeyState.argtypes = [ctypes.c_int]
    u.GetAsyncKeyState.restype = ctypes.c_short
    return u


class HotkeyWatcher(QObject):
    """盯着 Ctrl+Alt+B，按下那一刻回调一次。"""

    POLL_MS = 90

    def __init__(self, on_fire, parent=None):
        super().__init__(parent)
        self._on_fire = on_fire
        self._api = None
        self._held = False       # 上一次轮询时组合键是否已经按着

        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._poll)

    # ------------------------------------------------------------ 开关
    def set_enabled(self, on: bool) -> None:
        if on:
            self._held = False
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._held = False

    def is_enabled(self) -> bool:
        return self._timer.isActive()

    # ------------------------------------------------------------ 轮询
    def _key_down(self, vk: int) -> bool:
        if self._api is None:
            self._api = _user32()
        return bool(self._api.GetAsyncKeyState(vk) & 0x8000)

    def _poll(self) -> None:
        down = (
            self._key_down(VK_CONTROL)
            and self._key_down(VK_MENU)
            and self._key_down(VK_B)
        )
        if down and not self._held:
            self._held = True
            try:
                self._on_fire()
            except Exception:
                pass
        elif not down:
            self._held = False

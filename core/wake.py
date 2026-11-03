# -*- coding: utf-8 -*-
"""黑屏期间的「键盘 / 鼠标唤醒」检测。

为什么不用 Qt 的 keyPressEvent：遮罩窗口不一定拿着键盘焦点（Windows 会
把焦点给别的置顶窗口、输入法候选框、游戏、远程桌面），一旦丢了焦点，
按什么键都传不到我们这里，表现就是"按了键还黑着"。这里直接向系统要全局
状态（GetAsyncKeyState / GetCursorPos），跟焦点无关，任何窗口在前台都能
收到。

为什么要"基线 + 边沿"：黑屏是渐入的，出现的那一瞬间用户可能正按着
Shift、或者手还在鼠标上，如果只判断"当前有没有输入"，遮罩会立刻被自己
掀开。所以 arm() 时把当时的按键集合和光标位置记成基线，只有"新按下一
个键"或者"光标明显移动"才算唤醒。再加一个 guard 窗口，把黑屏最初几百
毫秒内的零碎输入全部吞掉。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os

_IS_WIN = os.name == "nt"

if _IS_WIN:  # pragma: no branch
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetCursorPos.restype = wt.BOOL
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]

# 0x01~0xFE 覆盖了鼠标左右中键、X 键、全部键盘键和小键盘。
# 单独排除鼠标键没有意义——我们要的就是"按了任何东西"。
_VK_LIST = tuple(range(0x01, 0xFF))

_KEY_DOWN = 0x8000
# 低位 0x0001 = "自上次调用以来被按过"。靠它才能抓到两次轮询之间的快速
# 敲击（按下又松开只要 20ms），只读 0x8000 会漏掉这种短按。
_KEY_HIT = 0x8001


def _tick32() -> int:
    """32 位系统 tick（毫秒）。GetLastInputInfo / GetTickCount 都是 32 位，
    统一用 32 位再做回绕安全的减法，避免系统跑满 49.7 天之后算出天文数字。"""
    return _kernel32.GetTickCount64() & 0xFFFFFFFF


def _since(tick: int) -> int:
    """距离 tick 过去了多少毫秒（32 位回绕安全）。"""
    return (_tick32() - tick) & 0xFFFFFFFF


def scan_keys() -> tuple[set[int], set[int]]:
    """一次遍历同时拿到 (当前按住, 上次查询后按过) 两组键。

    两组都要：只读"当前按住"会漏掉两次轮询之间的快速敲击（按下又松开
    只要 20ms）；只用"按过"的标志则会把已经松开的键当成新按键。读取本
    身会清除标志位，所以每次调用都相当于"消费"了这期间的事件。
    """
    held: set[int] = set()
    hit: set[int] = set()
    if not _IS_WIN:
        return held, hit
    try:
        for vk in _VK_LIST:
            state = _user32.GetAsyncKeyState(vk)
            if state & _KEY_DOWN:
                held.add(vk)
            if state & _KEY_HIT:
                hit.add(vk)
    except Exception:
        return set(), set()
    return held, hit


def pressed_keys(include_hit: bool = False) -> set[int]:
    """当前处于按下状态的虚拟键码集合（含鼠标键）。"""
    held, hit = scan_keys()
    return (held | hit) if include_hit else held


def cursor_pos() -> tuple[int, int]:
    if not _IS_WIN:
        return 0, 0
    try:
        pt = wt.POINT()
        if _user32.GetCursorPos(ctypes.byref(pt)):
            return int(pt.x), int(pt.y)
    except Exception:
        pass
    return 0, 0


class WakeWatcher:
    """黑屏期间的唤醒判定：arm() 建立基线，poll() 返回是否该解除黑屏。"""

    def __init__(self, guard_ms: int = 250, move_px: int = 14):
        self.guard_ms = guard_ms
        self.move_px = move_px
        self._armed = False
        self._t0 = 0
        self._keys: set[int] = set()
        self._pos = (0, 0)

    def arm(self) -> None:
        """开始监听。记下此刻的键鼠状态作为基线。"""
        self._t0 = _tick32()
        # 基线只取"当前真的按住"的键：黑屏前那一下敲击虽然还带着按过
        # 标志，但已经松开了，不该算成唤醒。读取同时清掉标志。
        self._keys, _ = scan_keys()
        self._pos = cursor_pos()
        self._armed = True

    def disarm(self) -> None:
        self._armed = False
        self._keys = set()

    @property
    def armed(self) -> bool:
        return self._armed

    def poll(self) -> bool:
        """是否有新的键鼠输入。命中一次后调用方应 disarm。"""
        if not _IS_WIN or not self._armed:
            return False

        if _since(self._t0) < self.guard_ms:
            # guard 期内把变化吸收进基线：黑屏刚出现的残留输入不算唤醒。
            # 这里只记"当前按住"的键，否则同一次敲击的按过标志会被存进
            # 基线，之后同一个键再按就永远识别不出来了。
            self._keys, _ = scan_keys()
            self._pos = cursor_pos()
            return False

        held, hit = scan_keys()
        if (held | hit) - self._keys:
            return True

        if self.move_px > 0:
            x, y = cursor_pos()
            if abs(x - self._pos[0]) >= self.move_px or abs(y - self._pos[1]) >= self.move_px:
                return True
        return False


if __name__ == "__main__":  # 手动验证：运行后按任意键/动鼠标
    import time

    w = WakeWatcher(guard_ms=0)
    w.arm()
    print("监听中…按任意键或移动鼠标退出")
    while True:
        if w.poll():
            print("检测到键鼠输入")
            break
        time.sleep(0.05)

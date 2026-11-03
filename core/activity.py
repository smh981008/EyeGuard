# -*- coding: utf-8 -*-
"""键鼠空闲时长。

为什么需要它：摄像头只能看到"人脸朝哪"，看不到"你在不在用电脑"。
而且 5 个关键点在侧脸、逆光、手挡时本来就不可靠。只要用户还在
敲键盘/动鼠标，就可以确定他没走——这是比任何视觉特征都硬的证据。

方向是单向的：活动只能阻止"在位 -> 离开"，绝不会把"离开"强行拉回
"在位"。否则人走了但后台程序在动鼠标，黑屏就永远触发不了。
"""
from __future__ import annotations

import ctypes
import os
import sys

_IS_WIN = os.name == "nt"

if _IS_WIN:  # pragma: no branch
    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.GetTickCount64.restype = ctypes.c_ulonglong


def idle_seconds() -> float:
    """距离上一次键盘/鼠标输入的秒数。取不到时返回 0（视为在用）。"""
    if not _IS_WIN:
        return 0.0
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        now = _kernel32.GetTickCount64()
        return max(0.0, (now - info.dwTime) / 1000.0)
    except Exception:
        return 0.0


def is_available() -> bool:
    if not _IS_WIN:
        return False
    try:
        return bool(_user32.GetLastInputInfo)
    except Exception:
        return False


if __name__ == "__main__":  # 手动验证用
    print("idle =", idle_seconds(), "available =", is_available())
    sys.exit(0)

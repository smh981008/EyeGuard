# -*- coding: utf-8 -*-
"""系统级动作：模拟按键、锁屏、关闭显示器。全部走 ctypes，无第三方依赖。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)

VK_MENU = 0x12      # Alt
VK_TAB = 0x09
VK_LWIN = 0x5B      # 左 Win
VK_D = 0x44
KEYEVENTF_KEYUP = 0x0002

HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170


def _key(vk: int, up: bool = False) -> None:
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP if up else 0, 0)


def _combo(modifier: int, key: int) -> None:
    _key(modifier)
    time.sleep(0.02)
    _key(key)
    time.sleep(0.02)
    _key(key, up=True)
    time.sleep(0.02)
    _key(modifier, up=True)


def alt_tab() -> None:
    """切换到下一个窗口。想切多个窗口可连续调用。"""
    _combo(VK_MENU, VK_TAB)


def show_desktop() -> None:
    """Win+D 显示桌面（最小化所有窗口）。"""
    _combo(VK_LWIN, VK_D)


def lock_workstation() -> None:
    user32.LockWorkStation()


def monitor_off() -> None:
    """关闭显示器（真正的息屏，比黑屏遮罩省电）。"""
    user32.SendMessageW(
        ctypes.wintypes.HWND(HWND_BROADCAST),
        WM_SYSCOMMAND,
        ctypes.wintypes.WPARAM(SC_MONITORPOWER),
        ctypes.wintypes.LPARAM(2),
    )


def run_actions(names: list[str]) -> None:
    """按顺序执行动作。锁屏/息屏放在最后，因为会中断后续输入模拟。"""
    order = ["black_screen", "switch_window", "show_desktop", "lock", "monitor_off"]
    for name in order:
        if name not in names:
            continue
        try:
            if name == "switch_window":
                alt_tab()
            elif name == "show_desktop":
                show_desktop()
            elif name == "lock":
                lock_workstation()
            elif name == "monitor_off":
                monitor_off()
        except Exception:
            pass


# ---------------------------------------------------------------- 开机自启
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "EyeGuard"


def set_autostart(enable: bool, exe_path: str) -> tuple[bool, str]:
    """写注册表 Run 键。返回 (是否成功, 说明)。"""
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enable:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, f'"{exe_path}"')
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True, ""
    except Exception as exc:
        return False, str(exc)


def is_autostart_on() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 虚拟桌面尺寸
def virtual_screen_rect() -> tuple[int, int, int, int]:
    """返回覆盖所有显示器的 (x, y, width, height)。"""
    sm = user32.GetSystemMetrics
    x = sm(76)              # SM_XVIRTUALSCREEN
    y = sm(77)              # SM_YVIRTUALSCREEN
    w = sm(78)              # SM_CXVIRTUALSCREEN
    h = sm(79)              # SM_CYVIRTUALSCREEN
    if w <= 0 or h <= 0:
        w, h = sm(0), sm(1)
        x = y = 0
    return x, y, w, h

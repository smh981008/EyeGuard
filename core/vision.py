# -*- coding: utf-8 -*-
"""头部朝向测量。

为什么不用 solvePnP：
  YuNet 只给 5 个关键点（双眼、鼻尖、左右嘴角），这 5 点在真实人脸上
  近似共面，3D 姿态解存在"镜像二义性"。实测侧脸超过约 40° 时，
  PnP 会稳定地收敛到"人脸倒转 180°"的镜像解（pitch/roll 跳到 ±165°），
  且三种解法（EPNP / SQPNP / ITERATIVE 带初值）结果一致——说明不是数值
  问题，而是这组点本身就支持两个解。

所以这里改用几何直测：所有量都以"眼距"归一化，不受距离和分辨率影响，
不存在解翻转。代价是需要一次基准校准，用来吸收每个人鼻梁高度、
摄像头俯角带来的固有偏移。
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

# YuNet 关键点下标
EYE_R, EYE_L, NOSE, MOUTH_R, MOUTH_L = 0, 1, 2, 3, 4

# 偏移比与转角的换算尺度。几何上 偏移比 ≈ scale · tan(θ)，
# 所以用 atan 反解，45° 以内误差很小，超过 45° 趋于 90° 但不会饱和死。
YAW_SCALE = 0.50
PITCH_SCALE = 0.32


def _ratio_to_deg(delta: float, scale: float) -> float:
    """归一化偏移比 -> 近似转角（度）。"""
    return math.degrees(math.atan2(delta, scale))


class FaceMetrics:
    """一帧人脸的朝向测量结果。角度均为相对校准基准的偏离量。"""

    __slots__ = ("roll", "yaw", "pitch", "yaw_i", "pitch_i", "eye_dist", "valid")

    def __init__(self, roll, yaw, pitch, yaw_i, pitch_i, eye_dist):
        self.roll = roll
        self.yaw = yaw
        self.pitch = pitch
        self.yaw_i = yaw_i
        self.pitch_i = pitch_i
        self.eye_dist = eye_dist
        self.valid = True


def analyze(landmarks, base_yaw_i: float = 0.0, base_pitch_i: float = 0.0):
    """从 5 个关键点算头部朝向。

    landmarks: 长度 10 的数组（x0,y0,...,x4,y4），YuNet 顺序。
    base_*: 校准得到的基准偏移比，正对屏幕时该值为 0。
    """
    pts = np.asarray(landmarks, dtype=np.float64).reshape(5, 2)
    er, el, nose = pts[EYE_R], pts[EYE_L], pts[NOSE]

    eye_vec = el - er
    eye_dist = float(np.hypot(eye_vec[0], eye_vec[1]))
    if eye_dist < 10.0:
        return None

    # 眼睛连线的倾角就是翻滚角，最可靠的一个量
    roll = math.degrees(math.atan2(eye_vec[1], eye_vec[0]))

    eye_mid = (er + el) / 2.0
    yaw_i = float((nose[0] - eye_mid[0]) / eye_dist)
    pitch_i = float((nose[1] - eye_mid[1]) / eye_dist)

    # 几何合理性检查：手挡脸、戴口罩、误检时关键点会错乱，
    # 此时算出来的角度纯属噪声，宁可不给结论。
    #
    # 注意范围要留得足够宽：侧脸到 70° 时鼻尖会明显跑出两眼连线之外，
    # 收得太紧会把"真的转开了"判成"读不出来"，而读不出来又会被
    # 当成"在位"，于是人转头后屏幕反而亮起来。这个坑踩过。
    x_lo, x_hi = min(er[0], el[0]), max(er[0], el[0])
    if not (x_lo - eye_dist * 2.6 <= nose[0] <= x_hi + eye_dist * 2.6):
        return None
    if not (eye_mid[1] - eye_dist * 0.6 < nose[1] < eye_mid[1] + eye_dist * 2.2):
        return None
    mouth_mid = (pts[MOUTH_R] + pts[MOUTH_L]) / 2.0
    if mouth_mid[1] < nose[1] - eye_dist * 0.20:
        return None

    yaw = _ratio_to_deg(yaw_i - base_yaw_i, YAW_SCALE)

    # 转头时眼距在图像上被 cos(yaw) 压扁，导致纵向偏移比虚高，
    # 不补偿的话侧脸 30° 会被误判成低头 18°。
    pitch_i_corr = pitch_i * math.cos(math.radians(yaw))
    pitch = _ratio_to_deg(pitch_i_corr - base_pitch_i, PITCH_SCALE)

    return FaceMetrics(roll, yaw, pitch, yaw_i, pitch_i, eye_dist)


def raw_ratios(landmarks) -> tuple[float, float] | None:
    """只取归一化偏移比，校准时用来记录基准。"""
    pts = np.asarray(landmarks, dtype=np.float64).reshape(5, 2)
    er, el, nose = pts[EYE_R], pts[EYE_L], pts[NOSE]
    eye_vec = el - er
    d = float(np.hypot(eye_vec[0], eye_vec[1]))
    if d < 4.0:
        return None
    mid = (er + el) / 2.0
    return float((nose[0] - mid[0]) / d), float((nose[1] - mid[1]) / d)


class Ema:
    """指数移动平均，抑制关键点抖动。"""

    def __init__(self, alpha: float = 0.45):
        self.alpha = alpha
        self.value: float | None = None

    def update(self, x: float) -> float:
        if self.value is None:
            self.value = x
        else:
            self.value = self.alpha * x + (1 - self.alpha) * self.value
        return self.value

    def reset(self) -> None:
        self.value = None


class Median:
    """滑动中位数。

    比 EMA 更适合这里：EMA 给单帧错值 45% 的权重，一个关键点抖飞的
    帧就能把角度顶过阈值（会误判离开）；中位数直接把这个值排除在
    结果之外，而且不留余波。
    """

    def __init__(self, size: int = 5):
        self._buf: deque[float] = deque(maxlen=size)

    def update(self, x: float) -> float:
        self._buf.append(float(x))
        return float(np.median(self._buf))

    def reset(self) -> None:
        self._buf.clear()

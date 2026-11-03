# -*- coding: utf-8 -*-
"""检测引擎：摄像头采集 + 人脸检测 + 在位/离开状态机。

运行在独立后台线程，通过回调把状态变化抛给 UI 层。

判定设计（这几个坑都踩过，注释保留原因）：
  1. "看不清"不能当成"在位"。5 点关键点在侧脸/逆光/手挡时经常给不出
     可靠朝向，如果这种帧按"没触发原因"处理，一次 0.2 秒的抖动就会
     把黑屏掀开——这正是"头转开了屏幕却自己亮起来"的成因。
  2. 进入离开用严格阈值，回到在位用宽松阈值（回差），否则在阈值附近
     徘徊时会一亮一灭。
  3. 键鼠活动只能阻止"在位 -> 离开"，绝不强行拉回"在位"。人在敲键盘
     就说明没走，但反过来，人转头而手在动鼠标也不该把黑屏掀开。
"""
from __future__ import annotations

import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import activity
from .config import resource_path
from .vision import Median, analyze

MODEL_FILE = "models/face_detection_yunet_2023mar.onnx"

# 校准需要的采样帧数。约 2 秒（检测间隔 120ms）。
CALIB_FRAMES = 16

# 小于这个像素尺寸的人脸框直接丢弃：关键点在这个尺度已经不可用，
# 而且这类框基本都是背景误检。
MIN_FACE_PX = 36
MIN_FACE_SCORE = 0.68

# 有脸但读不出朝向，要持续这么久才认作"看不到正面"。
# 比"人脸消失"更保守：读不出也可能只是手撑着脸、戴墨镜、逆光，
# 2.5 秒太急了（用户反馈过"手托着下巴看屏幕突然黑了"），给到 5 秒。
UNREADABLE_AWAY_AFTER = 5.0

# 就算键鼠在动，人脸彻底消失这么久也必须判定离开
# （人走了但后台有程序在模拟输入的情况）
HARD_ABSENT_AFTER = 8.0

# "正面"要连续成立这么久才算数。侧脸时 YuNet 偶尔会蹦出一两帧看着正常的
# 关键点，如果这种单帧就能把"离开"的计时清零，人一直转头也永远黑不了屏。
FRONT_CONFIRM = 0.30

STATE_PRESENT = "present"   # 在位
STATE_AWAY = "away"         # 离开
STATE_PAUSED = "paused"     # 暂停


def resolve_model_path() -> Path:
    """定位可用的模型文件。

    OpenCV 的 ONNX 加载器无法处理非 ASCII 路径（中文目录直接报
    "Can't read ONNX file"），所以路径含中文时先复制到系统临时目录。
    """
    src = resource_path(MODEL_FILE)
    if not src.exists():
        return src
    try:
        if str(src).isascii():
            return src
    except Exception:
        pass

    cache = Path(tempfile.gettempdir()) / "EyeGuardModel"
    dst = cache / src.name
    try:
        cache.mkdir(parents=True, exist_ok=True)
        needs_copy = (not dst.exists()) or dst.stat().st_size != src.stat().st_size
        if needs_copy:
            shutil.copy2(src, dst)
        return dst if dst.exists() else src
    except OSError:
        return src


@dataclass
class Snapshot:
    """某一时刻的检测结果快照，供 UI 渲染。"""
    state: str = STATE_PAUSED
    ready: bool = False          # 是否已经产出过至少一次判定
    face_found: bool = False
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    face_ratio: float = 0.0
    center_dist: float = 0.0
    away_for: float = 0.0
    present_for: float = 0.0
    reasons: list[str] = field(default_factory=list)
    fps: float = 0.0
    error: str = ""
    calibrating: bool = False    # 正在学习基准姿态
    calib_progress: float = 0.0  # 0~1
    pose_ok: bool = False        # 这一帧能不能测出朝向
    guard_blocked: bool = False  # 键鼠活动闸门刚刚拦下了一次误判
    idle: float = 0.0            # 键鼠空闲秒数
    active: bool = False         # 是否正在操作键鼠


class Detector:
    """摄像头 + YuNet + 姿态测量，输出"在位/离开"事件。"""

    def __init__(self, cfg, on_leave=None, on_return=None, on_error=None,
                 on_calibrated=None):
        self.cfg = cfg
        self.on_leave = on_leave or (lambda reasons: None)
        self.on_return = on_return or (lambda: None)
        self.on_error = on_error or (lambda msg: None)
        self.on_calibrated = on_calibrated or (lambda x, y: None)

        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.Lock()

        self._state = STATE_PAUSED
        self._snap = Snapshot()
        self._frame: np.ndarray | None = None

        self._med_yaw = Median(5)
        self._med_pitch = Median(5)

        # 校准基准：正对屏幕时鼻尖相对眼中点的归一化偏移比
        self._base_yaw_i = float(self.cfg["base_yaw_i"])
        self._base_pitch_i = float(self.cfg["base_pitch_i"])
        self._calib_samples: list[tuple[float, float]] = []
        self._calib_tries = 0
        self._need_calib = False

        self._away_since: float | None = None
        self._present_since: float | None = None
        self._bad_pose_since: float | None = None
        self._absent_since: float | None = None
        self._front_since: float | None = None
        self._ever_present = False
        self._last_detect = 0.0
        self._fps = 0.0

        self._detector = None
        self._det_size: tuple[int, int] | None = None

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(target=self._loop, name="detector", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._running.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)
        self._thread = None
        self._set_state(STATE_PAUSED)

    def pause(self) -> None:
        self.cfg["enabled"] = False
        self._set_state(STATE_PAUSED)

    def resume(self) -> None:
        self.cfg["enabled"] = True
        self._set_state(STATE_PRESENT)

    def calibrate(self) -> None:
        """重新学习基准姿态：接下来若干帧取中位数作为"正对屏幕"。

        基准用归一化偏移比而不是角度，因为它与距离、分辨率、
        摄像头焦距都无关，换机器也不用重算。
        """
        self._calib_samples.clear()
        self._calib_tries = 0
        self._need_calib = True
        self._med_yaw.reset()
        self._med_pitch.reset()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------ 对外只读
    def snapshot(self) -> Snapshot:
        with self._lock:
            s = self._snap
            # 动态计算计时，让 UI 上的进度连续变化
            now = time.monotonic()
            if self._away_since:
                s.away_for = now - self._away_since
            if self._present_since:
                s.present_for = now - self._present_since
            return s

    def latest_frame(self) -> np.ndarray | None:
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    # ------------------------------------------------------------ 内部
    def _set_state(self, state: str) -> None:
        with self._lock:
            self._state = state
            self._snap.state = state
        if state == STATE_PRESENT:
            self._away_since = None

    def _load_detector(self) -> bool:
        model = resolve_model_path()
        if not model.exists():
            self.on_error(f"缺少模型文件：{model}")
            return False
        try:
            self._detector = cv2.FaceDetectorYN.create(
                str(model), "", (320, 320), score_threshold=0.6, nms_threshold=0.3, top_k=50
            )
            self._det_size = None
            return True
        except Exception as exc:  # pragma: no cover
            self.on_error(f"加载人脸模型失败：{exc}")
            return False

    @staticmethod
    def _open_camera(index: int, width: int, height: int):
        """依次尝试 DSHOW / MSMF，Windows 上 DSHOW 打开更快。"""
        for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                ok, _ = cap.read()
                if ok:
                    return cap
            cap.release()
        return None

    def _loop(self) -> None:  # noqa: C901
        cap = None
        frame_times: list[float] = []

        while self._running.is_set():
            if not self.cfg["enabled"]:
                if cap is not None:
                    cap.release()
                    cap = None
                self._set_state(STATE_PAUSED)
                time.sleep(0.2)
                continue

            # 先确保模型就绪，否则后面读到的帧也没人处理
            if self._detector is None and not self._load_detector():
                if cap is not None:
                    cap.release()
                    cap = None
                time.sleep(2.0)
                continue

            if cap is None:
                try:
                    cap = self._open_camera(
                        int(self.cfg["camera_index"]),
                        int(self.cfg["camera_width"]),
                        int(self.cfg["camera_height"]),
                    )
                except Exception as exc:
                    self.on_error(f"打开摄像头失败：{exc}")
                    cap = None
                if cap is None:
                    self._set_state(STATE_PAUSED)
                    self.on_error("打不开摄像头，请检查是否被其他程序占用")
                    time.sleep(2.0)
                    continue
                self.on_error("")

            ok, frame = cap.read()
            if not ok or frame is None:
                cap.release()
                cap = None
                time.sleep(0.5)
                continue

            now = time.monotonic()
            frame_times.append(now)
            if len(frame_times) > 30:
                frame_times.pop(0)
            if len(frame_times) > 2:
                span = frame_times[-1] - frame_times[0]
                self._fps = (len(frame_times) - 1) / span if span > 0 else 0.0

            if self.cfg["preview_mirror"]:
                frame = cv2.flip(frame, 1)

            interval = max(0.05, int(self.cfg["detect_interval_ms"]) / 1000.0)
            if now - self._last_detect >= interval:
                self._last_detect = now
                self._process(frame, now)

            # 预览用帧：未启用预览时不拷贝，省内存带宽
            if self.cfg["show_preview"]:
                with self._lock:
                    self._frame = frame
            else:
                with self._lock:
                    self._frame = None

        if cap is not None:
            cap.release()

    def _feed_calibration(self, metrics) -> bool:
        """收集基准样本，攒够就应用。返回是否仍在校准中。

        基准取中位数而不是均值，个别抖飞的帧不会把结果带偏。

        只有用户主动点「校准」时才会走这里。不做自动学习是刻意的：
        摄像头在画面里的零点取决于摄像头装在哪、人坐在哪，机器无法
        分辨"正对屏幕"和"扭头看副屏"。一旦把看副屏的姿势学成基准，
        真正正对屏幕时反而会被判成转开——这比不校准更糟。
        所以默认用解剖学零点（鼻尖在双眼中点正下方），谁觉得不准
        就正对屏幕点一次校准。
        """
        if not self._need_calib:
            return False
        if metrics is None:
            return True

        self._calib_samples.append((metrics.yaw_i, metrics.pitch_i))
        if len(self._calib_samples) < CALIB_FRAMES:
            return True

        arr = np.asarray(self._calib_samples, dtype=np.float64)
        self._calib_samples = []
        by = float(np.median(arr[:, 0]))
        bp = float(np.median(arr[:, 1]))

        span_y = float(arr[:, 0].max() - arr[:, 0].min())
        span_p = float(arr[:, 1].max() - arr[:, 1].min())
        steady = span_y < 0.24 and span_p < 0.26
        sane = abs(by) < 1.20 and 0.10 < bp < 1.50

        self._calib_tries += 1
        if (steady and sane) or self._calib_tries >= 6:
            self._base_yaw_i = by
            self._base_pitch_i = bp
            self.cfg["base_yaw_i"] = round(by, 4)
            self.cfg["base_pitch_i"] = round(bp, 4)
            self.cfg["calibrated"] = True
            self._need_calib = False
            self.cfg.save()
            self._med_yaw.reset()
            self._med_pitch.reset()
            self._bad_pose_since = None
            self.on_calibrated(self._base_yaw_i, self._base_pitch_i)
            return False
        return True

    def _thresholds(self) -> tuple[float, float, float, float]:
        """返回 (进入 yaw, 退出 yaw, 进入 pitch, 退出 pitch)。

        进入用配置阈值，退出更宽松，形成回差。退出值还设了下限，
        免得阈值调得很小时回差把退出条件压到 0 度附近。
        """
        enter_y = max(12.0, float(self.cfg["yaw_threshold"]))
        enter_p = max(12.0, float(self.cfg["pitch_threshold"]))
        exit_y = max(7.0, min(enter_y * 0.60, enter_y - 5.0))
        exit_p = max(7.0, min(enter_p * 0.60, enter_p - 5.0))
        return enter_y, exit_y, enter_p, exit_p

    def _process(self, frame: np.ndarray, now: float) -> None:  # noqa: C901
        h, w = frame.shape[:2]
        faces, landmarks = self._detect(frame)

        face_found = faces is not None and len(faces) > 0
        reasons: list[str] = []
        metrics = None
        face_ratio = 0.0
        center_dist = 1.0
        yaw = pitch = roll = 0.0

        if face_found:
            # 取面积最大的人脸（避免背景路人干扰）
            idx = int(np.argmax(faces[:, 2] * faces[:, 3]))
            fx, fy, fw, fh = faces[idx, :4]
            face_ratio = float(fw) / w
            cx = (fx + fw / 2.0) / w
            cy = (fy + fh / 2.0) / h
            center_dist = float(np.hypot(cx - 0.5, cy - 0.5) * 2.0)

            metrics = analyze(landmarks[idx], self._base_yaw_i, self._base_pitch_i)
            if metrics is None:
                if self._bad_pose_since is None:
                    self._bad_pose_since = now
            else:
                self._bad_pose_since = None
        else:
            self._bad_pose_since = None

        if not face_found:
            if self._absent_since is None:
                self._absent_since = now
        else:
            self._absent_since = None

        calibrating = self._feed_calibration(metrics)

        if metrics is not None:
            yaw = self._med_yaw.update(metrics.yaw)
            pitch = self._med_pitch.update(metrics.pitch)
            roll = metrics.roll
        else:
            self._med_yaw.reset()
            self._med_pitch.reset()

        enter_y, exit_y, enter_p, exit_p = self._thresholds()
        # 已经黑屏时必须"明确正对"才恢复：用更宽松的退出阈值
        lim_y = exit_y if self._state == STATE_AWAY else enter_y
        lim_p = exit_p if self._state == STATE_AWAY else enter_p

        # ------------------------------------------------ 视觉证据
        away_ev = False   # 有证据表明"人不在/没看屏幕"
        front_ev = False  # 有证据表明"人正对着屏幕"

        if not face_found:
            if self.cfg["check_face_missing"]:
                away_ev = True
                reasons.append("画面中没有人脸")
        elif calibrating:
            pass  # 校准中不下结论
        elif self.cfg["check_face_size"] and face_ratio < float(self.cfg["min_face_ratio"]):
            away_ev = True
            reasons.append(f"人脸过小（占宽 {face_ratio * 100:.0f}%）")
        elif metrics is None:
            # 有脸但读不出朝向：手挡、逆光，或者已经侧到看不清正面。
            # 先按"看不清"处理（既不算在也不离离开），持续久了才判离开。
            if now - (self._bad_pose_since or now) >= UNREADABLE_AWAY_AFTER:
                away_ev = True
                reasons.append("看不到正面（侧脸或被遮挡）")
        else:
            if not self.cfg["check_head_pose"]:
                front_ev = True
            elif abs(yaw) > lim_y:
                away_ev = True
                reasons.append(f"头部转开 {abs(yaw):.0f}°")
            elif abs(pitch) > lim_p:
                away_ev = True
                word = "低头" if pitch > 0 else "抬头"
                reasons.append(f"{word} {abs(pitch):.0f}°")
            else:
                front_ev = True

            if (
                not away_ev
                and self.cfg["check_center"]
                and center_dist > float(self.cfg["center_threshold"])
            ):
                away_ev = True
                reasons.append("人偏离画面中心")

        # ------------------------------------------------ 键鼠活动闸门
        # 只做"否决"：正打字/动鼠标时不判离开，但也不会把黑屏掀开。
        try:
            idle = activity.idle_seconds()
        except Exception:
            idle = 0.0
        active = bool(self.cfg["check_activity"]) and idle < float(self.cfg["activity_window"])

        guard_blocked = False
        if away_ev and active:
            absent_for = 0.0 if self._absent_since is None else now - self._absent_since
            # 人脸彻底消失太久就不能再拿"键鼠在动"解释（人可能已经走了）
            if face_found or absent_for < HARD_ABSENT_AFTER:
                away_ev = False
                reasons = []
                guard_blocked = True

        # ------------------------------------------------ 计时
        if front_ev:
            if self._front_since is None:
                self._front_since = now
        else:
            self._front_since = None

        if away_ev:
            self._present_since = None
            if self._away_since is None:
                self._away_since = now
        elif front_ev:
            # 只有"正面"稳定成立一小段时间，才把离开的计时清掉
            if self._front_since is not None and now - self._front_since >= FRONT_CONFIRM:
                self._away_since = None
            if self._present_since is None:
                self._present_since = now
        else:
            # 看不清 / 校准中 / 被活动闸门否掉：不动当前状态，
            # 但"回来"的计时必须清零——看不清绝不能算作"人在看屏幕"。
            self._present_since = None

        leave_delay = max(0.8, float(self.cfg["leave_delay"]))
        return_delay = max(0.8, float(self.cfg["return_delay"]))

        # ------------------------------------------------ 状态迁移
        if self._state == STATE_PAUSED:
            # 线程刚起来，第一次拿到判定结果就进入正常状态机
            self._set_state(STATE_PRESENT)
        elif self._state == STATE_PRESENT and away_ev and self._away_since is not None:
            if now - self._away_since >= leave_delay:
                self._set_state(STATE_AWAY)
                # 启动时如果人本来就不在，不要立刻糊一层黑屏；
                # 等他真正出现过一次之后才开始防护
                if self._ever_present:
                    self.on_leave(reasons)
        elif self._state == STATE_AWAY and front_ev and self._present_since is not None:
            if now - self._present_since >= return_delay:
                self._set_state(STATE_PRESENT)
                self.on_return()

        if front_ev:
            self._ever_present = True

        # ------------------------------------------------ 更新快照
        with self._lock:
            self._snap = Snapshot(
                state=self._state,
                ready=True,
                face_found=face_found,
                yaw=yaw,
                pitch=pitch,
                roll=roll,
                face_ratio=face_ratio,
                center_dist=center_dist,
                away_for=(0.0 if self._away_since is None else now - self._away_since),
                present_for=(0.0 if self._present_since is None else now - self._present_since),
                reasons=reasons,
                fps=self._fps,
                error="",
                calibrating=calibrating,
                calib_progress=min(1.0, len(self._calib_samples) / float(CALIB_FRAMES)),
                pose_ok=metrics is not None,
                guard_blocked=guard_blocked,
                idle=idle,
                active=active,
            )
            if self.cfg["show_preview"]:
                self._frame = self._annotate(frame, faces, landmarks, reasons, away_ev)

    def _detect(self, frame: np.ndarray):
        """返回 (faces[N,15], landmarks[N,10])，检测不到时返回 (None, None)。"""
        h, w = frame.shape[:2]
        if self._det_size != (w, h):
            self._detector.setInputSize((w, h))
            self._det_size = (w, h)
        try:
            _, faces = self._detector.detect(frame)
        except Exception:
            return None, None
        if faces is None or len(faces) == 0:
            return None, None

        # 滤掉太小的框：背景里的海报、贴纸、显示器上的图片经常被误检成
        # 人脸，一旦被当成"最大的人脸"就会把真正的判定带偏。这个尺度
        # 以下的框本来也提不出可用的关键点。
        keep = (faces[:, 2] >= MIN_FACE_PX) & (faces[:, 3] >= MIN_FACE_PX) & (
            faces[:, 14] >= MIN_FACE_SCORE
        )
        faces = faces[keep]
        if len(faces) == 0:
            return None, None
        # faces[:, 4:14] 是 5 个关键点
        return faces, faces[:, 4:14]

    @staticmethod
    def _annotate(frame, faces, landmarks, reasons, away) -> np.ndarray:
        img = frame.copy()
        color = (60, 60, 230) if away else (80, 200, 120)
        if faces is not None:
            for f in faces:
                x, y, w, h = f[:4].astype(int)
                cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
        text = "AWAY" if away else "PRESENT"
        if reasons:
            text += " · " + reasons[0]
        cv2.putText(
            img, text, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA
        )
        return img

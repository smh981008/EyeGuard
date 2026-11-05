# -*- coding: utf-8 -*-
"""设置窗口（PySide6 实现）。"""
from __future__ import annotations

import cv2
from PySide6.QtCore import QTime, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from core.config import APP_NAME, APP_VERSION
from core.detector import STATE_AWAY, STATE_PAUSED, STATE_PRESENT
from core.payroll import (
    countdown_text,
    money,
    money_daily,
    schedule_line,
    snapshot as pay_snapshot,
)
from core.schedule import DAY_SHORT, days_set, describe_now, in_window
from ui.theme import ACCENT, OK, QSS, SUB, WARN


class SettingsWindow(QWidget):
    """主设置面板。控件直接读写 app.cfg，改动即时生效。"""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cfg = app.cfg
        self._preview_on = False
        self._allow_close = False

        self.setObjectName("root")
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setStyleSheet(QSS)
        self.resize(700, 792)
        self.setMinimumSize(640, 600)

        self._build()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(110)

    # ================================================================ 构建
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(14)

        # ---------------- 头部
        head = QHBoxLayout()
        head.setSpacing(12)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        t = QLabel(APP_NAME)
        t.setObjectName("title")
        s = QLabel("人脸 / 视线离开电脑时，自动黑屏、切换窗口或锁屏")
        s.setObjectName("subtitle")
        titles.addWidget(t)
        titles.addWidget(s)
        head.addLayout(titles)
        head.addStretch()

        self.badge = QLabel("初始化")
        self.badge.setAlignment(Qt.AlignCenter)
        head.addWidget(self.badge, 0, Qt.AlignTop)
        root.addLayout(head)

        # ---------------- 标签页
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs, 1)

        self._tab_actions()
        self._tab_rules()
        self._tab_schedule()
        self._tab_pay()
        self._tab_preview()

        # ---------------- 底部按钮
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.enable_btn = QPushButton("启用检测")
        self.enable_btn.setObjectName("primary")
        self.enable_btn.setCursor(Qt.PointingHandCursor)
        self.enable_btn.clicked.connect(self._toggle_enable)
        bar.addWidget(self.enable_btn)

        test_btn = QPushButton("测试触发")
        test_btn.setObjectName("ghost")
        test_btn.setCursor(Qt.PointingHandCursor)
        test_btn.clicked.connect(self.app.test_trigger)
        bar.addWidget(test_btn)

        bar.addStretch()

        self.msg = QLabel("")
        self.msg.setObjectName("hint")
        bar.addWidget(self.msg)

        save_btn = QPushButton("保存设置")
        save_btn.setObjectName("ghost")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._save)
        bar.addWidget(save_btn)

        root.addLayout(bar)
        self._refresh_enable_btn()

    # ------------------------------------------------------------ 触发动作页
    def _tab_actions(self) -> None:
        page, lay = self._scroll_page()

        card, cl = self._card("检测到离开后执行")
        for key, label in [
            ("action_black_screen", "黑屏遮罩  ·  推荐，不影响任何程序，回来自动消失"),
            ("action_switch_window", "切换到下一个窗口  ·  Alt+Tab"),
            ("action_show_desktop", "显示桌面 / 最小化所有窗口  ·  Win+D"),
            ("action_lock", "锁定电脑"),
            ("action_monitor_off", "关闭显示器  ·  真息屏，需要手动唤醒"),
        ]:
            cl.addWidget(self._check(key, label))
        lay.addWidget(card)

        card, cl = self._card("时间防抖（避免喝水、低头就被黑屏）")
        self._slider(cl, "leave_delay", "离开持续多久后触发", 0.8, 30.0, 0.5, " 秒")
        self._slider(cl, "return_delay", "回来持续多久后恢复", 0.8, 10.0, 0.2, " 秒")
        lay.addWidget(card)

        card, cl = self._card("黑屏时显示的文字")
        self.text_input = QLineEdit(str(self.cfg["black_screen_text"]))
        self.text_input.textChanged.connect(
            lambda v: self.cfg.__setitem__("black_screen_text", v)
        )
        cl.addWidget(self.text_input)
        lay.addWidget(card)

        card, cl = self._card("黑屏怎么解除")
        self._check(
            "wake_on_input",
            "按任意键 / 移动鼠标即可解除（关掉后只能按 ESC）",
            cl,
        )
        self._slider(
            cl, "wake_rearm_seconds", "解除后多久没操作、人仍不在就重新黑屏",
            0, 60, 1, " 秒", zero_text="不重新黑屏",
        )
        wake_tip = QLabel(
            "键鼠检测是全局的，不依赖窗口焦点，所以黑屏时按的任何键、任何一次点击都能"
            "把你放出来。上面那项默认关闭：按了键就是要看屏幕，不该几秒后又黑下来；"
            "如果你担心起身时不小心碰到鼠标让保护失效，可以设成 20~30 秒。"
        )
        wake_tip.setObjectName("hint")
        wake_tip.setWordWrap(True)
        cl.addWidget(wake_tip)
        lay.addWidget(card)

        lay.addStretch()
        self.tabs.addTab(page, "  触发动作  ")

    # ------------------------------------------------------------ 判定规则页
    def _tab_rules(self) -> None:
        page, lay = self._scroll_page()

        self.cal_banner = QLabel(
            "先用一次校准，结果会更准：正对屏幕坐好，点下面的「校准当前姿态为基准」，保持约 2 秒别转头。"
        )
        self.cal_banner.setWordWrap(True)
        self.cal_banner.setStyleSheet(
            f"background: {ACCENT}12; color: {ACCENT}; border: 1px solid {ACCENT}3d;"
            " border-radius: 8px; padding: 10px 13px; font-size: 12px;"
        )
        lay.addWidget(self.cal_banner)

        card, cl = self._card("什么情况算「人离开了」")
        self._check("check_face_missing", "画面里检测不到人脸", cl)
        self._check("check_face_size", "人脸太小（人坐太远或已起身）", cl)
        self._check("check_head_pose", "头部明显转开也算离开（会误判「看副屏/看手机」，建议先关着）", cl)
        self._check("check_center", "人脸偏离画面中心（人侧身离开）", cl)
        lay.addWidget(card)

        card, cl = self._card("防误判")
        self._check("check_activity", "正在敲键盘 / 动鼠标时，绝不判定为离开（建议开启）", cl)
        act_tip = QLabel(
            "这是最有效的一道保险。摄像头只看得到「脸朝哪」，看不到「你在不在用」，"
            "而侧脸、逆光、手挡时关键点本来就不可靠。只要这段时间内有键鼠输入，就不触发。"
            "它只阻止「误黑屏」，不会把黑屏重新点亮。"
        )
        act_tip.setObjectName("hint")
        act_tip.setWordWrap(True)
        cl.addWidget(act_tip)
        self._slider(cl, "activity_window", "多久内有输入算「正在用」", 2.0, 30.0, 1.0, " 秒")
        lay.addWidget(card)

        card, cl = self._card("阈值")
        self._slider(cl, "min_face_ratio", "人脸宽度占画面比例下限", 0.04, 0.5, 0.01,
                     "", percent=True)
        self._slider(cl, "yaw_threshold", "左右转头阈值", 15, 80, 1, "°")
        self._slider(cl, "pitch_threshold", "抬头 / 低头阈值", 15, 70, 1, "°")
        self._slider(cl, "center_threshold", "偏离画面中心阈值", 0.1, 0.9, 0.01, "")
        hint = QLabel("数值越小越灵敏。看副屏或低头看键盘时，头本来就会偏 30~60°，"
                     "所以阈值要定得比它大。想让「视线移开」也黑屏，调到 35~45° 并勾上上面那项。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        cl.addWidget(hint)
        lay.addWidget(card)

        card, cl = self._card("姿态基准校准")
        tip = QLabel("默认基准假设「鼻尖在双眼中点正下方」就是正对屏幕，对大多数摄像头都够用。只有当你正对屏幕时预览里的角度明显不是 0°，才需要坐正后点一次校准。")
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        cl.addWidget(tip)

        row = QHBoxLayout()
        cal_btn = QPushButton("校准当前姿态为基准")
        cal_btn.setObjectName("ghost")
        cal_btn.setCursor(Qt.PointingHandCursor)
        cal_btn.clicked.connect(self.app.calibrate)
        row.addWidget(cal_btn)
        self.cal_label = QLabel("")
        self.cal_label.setObjectName("hint")
        row.addWidget(self.cal_label)
        row.addStretch()
        cl.addLayout(row)
        lay.addWidget(card)

        lay.addStretch()
        self.tabs.addTab(page, "  判定规则  ")

    # ------------------------------------------------------------ 定时计划页
    def _tab_schedule(self) -> None:
        page, lay = self._scroll_page()

        card, cl = self._card("运行时间")
        self.schedule_check = self._check(
            "schedule_enabled",
            "只在下面设定的时间里运行检测，其余时间自动暂停",
            cl,
        )
        self.schedule_check.toggled.connect(lambda _: self.app.check_schedule_now())

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("从"))
        self.time_start = self._time_edit("schedule_start")
        row.addWidget(self.time_start)
        row.addWidget(QLabel("到"))
        self.time_end = self._time_edit("schedule_end")
        row.addWidget(self.time_end)
        row.addStretch()
        cl.addLayout(row)

        days_row = QHBoxLayout()
        days_row.setSpacing(6)
        picked = days_set(self.cfg)
        self.day_boxes: list[QPushButton] = []
        for i, name in enumerate(DAY_SHORT):
            btn = QPushButton(name)
            btn.setObjectName("day")
            btn.setCheckable(True)
            btn.setChecked(i in picked)
            btn.setFixedSize(38, 30)
            btn.setCursor(Qt.PointingHandCursor)
            btn.toggled.connect(lambda v, d=i: self._set_day(d, v))
            days_row.addWidget(btn)
            self.day_boxes.append(btn)
        days_row.addStretch()
        cl.addLayout(days_row)

        quick = QHBoxLayout()
        quick.setSpacing(6)
        for label, picks in (
            ("每天", list(range(7))),
            ("工作日", list(range(5))),
            ("周末", [5, 6]),
        ):
            btn = QPushButton(label)
            btn.setObjectName("ghost")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, p=picks: self._apply_days(p))
            quick.addWidget(btn)
        quick.addStretch()
        cl.addLayout(quick)
        lay.addWidget(card)

        card, cl = self._card("当前状态")
        self.schedule_status = QLabel("")
        self.schedule_status.setWordWrap(True)
        cl.addWidget(self.schedule_status)

        tip = QLabel(
            "结束时间早于开始时间就是跨夜，比如 22:00 到 06:00 算到第二天早上，"
            "星期按开始那天算（勾了周一，周一 22:00 到周二 06:00 都在时段内）。\n\n"
            "计划只管自动启停：时段外你仍然可以手动点「启用检测」临时用一会儿，"
            "不会被立刻覆盖，到下一个时间边界才交回计划。程序本身一直在托盘里运行，"
            "不需要退出。"
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        cl.addWidget(tip)
        lay.addWidget(card)

        lay.addStretch()
        self.tabs.addTab(page, "  定时计划  ")
        self._refresh_schedule_status()

    # ------------------------------------------------------------ 上班鼓励器页
    def _tab_pay(self) -> None:
        page, lay = self._scroll_page()

        card, cl = self._card("黑屏显示")
        self.pay_check = self._check(
            "pay_enabled",
            "黑屏时显示今天赚了多少钱、还剩多久下班",
            cl,
        )

        money_row = QHBoxLayout()
        money_row.setSpacing(10)
        money_row.addWidget(QLabel("月薪"))
        self.pay_money = QLineEdit()
        self.pay_money.setPlaceholderText("例如 12000")
        self.pay_money.setMaxLength(9)
        self.pay_money.setFixedWidth(150)
        monthly = float(self.cfg["pay_monthly"])
        self.pay_money.setText(f"{monthly:g}" if monthly > 0 else "")
        self.pay_money.textChanged.connect(self._on_pay_money)
        money_row.addWidget(self.pay_money)
        money_row.addWidget(QLabel("元 / 月"))
        money_row.addStretch()
        cl.addLayout(money_row)

        days_row = QHBoxLayout()
        days_row.setSpacing(10)
        days_row.addWidget(QLabel("每月计薪天数"))
        self.pay_mdays = QLineEdit()
        self.pay_mdays.setFixedWidth(80)
        self.pay_mdays.setText(f"{float(self.cfg['pay_month_days']):g}")
        self.pay_mdays.textChanged.connect(self._on_pay_mdays)
        days_row.addWidget(self.pay_mdays)
        days_row.addWidget(QLabel("天"))
        days_row.addStretch()
        cl.addLayout(days_row)

        mdays_tip = QLabel(
            "5 天工作制按 21.75 天（法定月计薪天数），单休约 26 天，"
            "大小周取中间值。这个数字只影响时薪高低，不影响上下班判断。"
        )
        mdays_tip.setObjectName("hint")
        mdays_tip.setWordWrap(True)
        cl.addWidget(mdays_tip)
        lay.addWidget(card)

        card, cl = self._card("上下班时间")
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("从"))
        self.pay_start_edit = self._time_edit("pay_start", schedule=False)
        row.addWidget(self.pay_start_edit)
        row.addWidget(QLabel("到"))
        self.pay_end_edit = self._time_edit("pay_end", schedule=False)
        row.addWidget(self.pay_end_edit)
        row.addStretch()
        cl.addLayout(row)

        # 午休分段：勾上之后，中间那段不计工时，进度条会停住
        self.pay_break_check = self._check(
            "pay_break_enabled", "中间有午休，下午再来一段（休息时间不算工时）", cl
        )
        self.pay_break_check.toggled.connect(self._toggle_pay_break)

        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)
        hl.addSpacing(22)
        hl.addWidget(QLabel("下午从"))
        self.pay_start2_edit = self._time_edit("pay_start2", schedule=False)
        hl.addWidget(self.pay_start2_edit)
        hl.addWidget(QLabel("到"))
        self.pay_end2_edit = self._time_edit("pay_end2", schedule=False)
        hl.addWidget(self.pay_end2_edit)
        hl.addStretch()
        cl.addWidget(holder)
        self.pay_break_box = holder
        holder.setVisible(bool(self.cfg["pay_break_enabled"]))

        pick = {int(d) for d in (self.cfg["pay_days"] or [])}
        day_row = QHBoxLayout()
        day_row.setSpacing(6)
        self.pay_day_boxes: list[QPushButton] = []
        for i, name in enumerate(DAY_SHORT):
            btn = QPushButton(name)
            btn.setObjectName("day")
            btn.setCheckable(True)
            btn.setChecked(i in pick)
            btn.setFixedSize(38, 30)
            btn.setCursor(Qt.PointingHandCursor)
            btn.toggled.connect(lambda v, d=i: self._set_pay_day(d, v))
            day_row.addWidget(btn)
            self.pay_day_boxes.append(btn)
        day_row.addStretch()
        cl.addLayout(day_row)

        quick = QHBoxLayout()
        quick.setSpacing(6)
        for label, picks in (("每天", list(range(7))), ("工作日", list(range(5))),
                             ("单休", list(range(6)))):
            btn = QPushButton(label)
            btn.setObjectName("ghost")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, p=picks: self._apply_pay_days(p))
            quick.addWidget(btn)
        quick.addStretch()
        cl.addLayout(quick)
        lay.addWidget(card)

        card, cl = self._card("换算结果")
        self.pay_preview = QLabel("")
        self.pay_preview.setObjectName("metric")
        self.pay_preview.setWordWrap(True)
        cl.addWidget(self.pay_preview)

        tip = QLabel(
            "钱按“坐在工位上的时间”连续折算：今天已赚 = 日薪 × 已过去的比例，"
            "所以黑屏上的数字会一秒一秒往上爬。\n\n"
            "勾上「中间有午休」就是分段算：08:30–11:30 + 14:00–17:30 一共 6.5 小时，"
            "午休那一个半小时不产生收入，黑屏上的进度条和金额会停住，下午开工再接着走。"
            "不勾则从上班到下班一口气算，午休也算工时。\n\n"
            "想先看看效果，点底部的「测试触发」，黑屏会立刻弹出来，3 秒后自动收起。"
        )
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        cl.addWidget(tip)
        lay.addWidget(card)

        lay.addStretch()
        self.tabs.addTab(page, "  上班鼓励器  ")
        self._refresh_pay_preview()

    # ------------------------------------------------------------ 预览页
    def _tab_preview(self) -> None:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(6, 12, 6, 6)
        lay.setSpacing(10)

        top = QHBoxLayout()
        label = QLabel("摄像头")
        top.addWidget(label)

        self.cam_combo = QComboBox()
        for i in range(4):
            self.cam_combo.addItem(f"摄像头 {i}", i)
        self.cam_combo.setCurrentIndex(int(self.cfg["camera_index"]))
        self.cam_combo.currentIndexChanged.connect(self._on_cam_change)
        top.addWidget(self.cam_combo)
        top.addStretch()

        self.preview_check = QCheckBox("开启画面预览")
        self.preview_check.toggled.connect(self._toggle_preview)
        top.addWidget(self.preview_check)
        lay.addLayout(top)

        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(8, 8, 8, 8)

        self.video = QLabel("预览未开启\n\n勾选右上角后可以看到实时画面和判定结果")
        self.video.setAlignment(Qt.AlignCenter)
        self.video.setMinimumHeight(260)
        self.video.setStyleSheet(
            "background: #0d1014; color: #4b5563; border-radius: 8px; font-size: 12px;"
        )
        cl.addWidget(self.video, 1)
        lay.addWidget(card, 1)

        self.metrics = QLabel("")
        self.metrics.setObjectName("metric")
        self.metrics.setWordWrap(True)
        lay.addWidget(self.metrics)

        self.reasons = QLabel("")
        self.reasons.setObjectName("hint")
        self.reasons.setWordWrap(True)
        lay.addWidget(self.reasons)

        self.tabs.addTab(page, "  实时预览  ")

    # ================================================================ 组件
    def _scroll_page(self) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(6, 12, 6, 12)
        lay.setSpacing(12)
        scroll.setWidget(inner)
        return scroll, lay

    def _card(self, title: str = "") -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("card")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(18, 15, 18, 15)
        lay.setSpacing(9)
        if title:
            lab = QLabel(title)
            lab.setObjectName("section")
            lay.addWidget(lab)
        return frame, lay

    def _check(self, key: str, text: str, parent_lay=None) -> QCheckBox:
        cb = QCheckBox(text)
        cb.setChecked(bool(self.cfg[key]))
        cb.toggled.connect(lambda v, k=key: self.cfg.__setitem__(k, bool(v)))
        cb.setCursor(Qt.PointingHandCursor)
        if parent_lay is not None:
            parent_lay.addWidget(cb)
        return cb

    def _slider(self, lay, key: str, label: str, lo: float, hi: float,
                step: float, unit: str, percent: bool = False,
                zero_text: str | None = None) -> QSlider:
        row = QVBoxLayout()
        row.setSpacing(4)

        top = QHBoxLayout()
        name = QLabel(label)
        val = QLabel()
        val.setObjectName("value")
        val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(name)
        top.addStretch()
        top.addWidget(val)
        row.addLayout(top)

        count = int(round((hi - lo) / step))
        slider = QSlider(Qt.Horizontal)
        slider.setRange(0, count)
        slider.setPageStep(1)

        def fmt(v: float) -> str:
            if zero_text is not None and v <= lo:
                return zero_text
            if percent:
                return f"{v * 100:.0f}%"
            text = f"{v:g}{unit}"
            return text

        def on_change(idx: int) -> None:
            value = round(lo + idx * step, 4)
            self.cfg[key] = value
            val.setText(fmt(value))

        slider.valueChanged.connect(on_change)
        slider.setValue(int(round((float(self.cfg[key]) - lo) / step)))
        val.setText(fmt(float(self.cfg[key])))

        row.addWidget(slider)
        lay.addLayout(row)
        return slider

    def _time_edit(self, key: str, schedule: bool = True) -> QTimeEdit:
        edit = QTimeEdit()
        edit.setDisplayFormat("HH:mm")
        t = QTime.fromString(str(self.cfg[key]), "HH:mm")
        if t.isValid():
            edit.setTime(t)

        def on_change(t: QTime) -> None:
            self.cfg[key] = t.toString("HH:mm")
            if schedule:
                self._refresh_schedule_status()
                self.app.check_schedule_now()
            else:
                self._refresh_pay_preview()

        edit.timeChanged.connect(on_change)
        return edit

    # ================================================================ 交互
    def _on_cam_change(self, index: int) -> None:
        self.cfg["camera_index"] = int(self.cam_combo.itemData(index) or 0)

    # ------------------------------------------------------------ 定时计划
    def _set_day(self, day: int, on: bool) -> None:
        days = {int(d) for d in self.cfg["schedule_days"]}
        if on:
            days.add(day)
        else:
            days.discard(day)
        if not days:
            # 一天都不勾，语义上等于"每天都开"，很容易被误解成"从不运行"。
            # 与其留个会让软件永远不工作的状态，直接恢复全选。
            days = set(range(7))
            self._sync_day_boxes(days)
        self.cfg["schedule_days"] = sorted(days)
        self._refresh_schedule_status()
        self.app.check_schedule_now()

    def _apply_days(self, picks) -> None:
        days = {int(d) for d in picks}
        self._sync_day_boxes(days)
        self.cfg["schedule_days"] = sorted(days)
        self._refresh_schedule_status()
        self.app.check_schedule_now()

    def _sync_day_boxes(self, days: set[int]) -> None:
        for i, btn in enumerate(self.day_boxes):
            btn.blockSignals(True)
            btn.setChecked(i in days)
            btn.blockSignals(False)

    # ------------------------------------------------------------ 上班鼓励器
    def _on_pay_money(self, text: str) -> None:
        digits = "".join(ch for ch in text if ch.isdigit())
        self.cfg["pay_monthly"] = float(digits) if digits else 0.0
        self._refresh_pay_preview()

    def _on_pay_mdays(self, text: str) -> None:
        raw = text.strip()
        try:
            value = float(raw)
        except ValueError:
            value = 0.0
        # 越界先存默认值，等用户输完再说；不能写 0 进去，否则时薪会变成无穷大
        self.cfg["pay_month_days"] = value if 1.0 <= value <= 31.0 else 21.75
        self._refresh_pay_preview()

    def _set_pay_day(self, day: int, on: bool) -> None:
        days = {int(d) for d in (self.cfg["pay_days"] or [])}
        if on:
            days.add(day)
        else:
            days.discard(day)
        if not days:
            days = set(range(7))       # 同定时计划：空集合等于每天
            self._sync_pay_days(days)
        self.cfg["pay_days"] = sorted(days)
        self._refresh_pay_preview()

    def _apply_pay_days(self, picks) -> None:
        days = {int(d) for d in picks}
        self._sync_pay_days(days)
        self.cfg["pay_days"] = sorted(days)
        self._refresh_pay_preview()

    def _sync_pay_days(self, days: set[int]) -> None:
        for i, btn in enumerate(self.pay_day_boxes):
            btn.blockSignals(True)
            btn.setChecked(i in days)
            btn.blockSignals(False)

    def _toggle_pay_break(self, on: bool) -> None:
        self.cfg["pay_break_enabled"] = bool(on)
        self.pay_break_box.setVisible(bool(on))
        self._refresh_pay_preview()

    def _refresh_pay_preview(self) -> None:
        if not hasattr(self, "pay_preview"):
            return
        snap = pay_snapshot(self.cfg)
        if not snap.ok:
            text = "还没填月薪，黑屏上不会出现工资板。填好后这里会实时显示换算结果。"
        else:
            text = (
                f"时段 {schedule_line(self.cfg)}，共 {snap.hours:g} 小时\n"
                f"日薪 {money_daily(snap.daily)}  =  月薪 {money(snap.monthly)} ÷ {snap.days:g} 天\n"
                f"时薪 {money(snap.hourly)}  =  日薪 ÷ {snap.hours:g} 小时\n"
                f"{countdown_text(snap)}   ·   今天已赚 {money(snap.earned)}"
            )
        if self.pay_preview.text() != text:
            self.pay_preview.setText(text)

    def _toggle_preview(self, on: bool) -> None:
        self._preview_on = on
        self.cfg["show_preview"] = on
        if not on:
            self.video.setPixmap(QPixmap())
            self.video.setText("预览未开启")

    def _toggle_enable(self) -> None:
        self.app.toggle_enabled()
        self._refresh_enable_btn()

    def _refresh_enable_btn(self) -> None:
        if self.cfg["enabled"]:
            self.enable_btn.setText("运行中 · 点击暂停")
            self.enable_btn.setObjectName("success")
        else:
            self.enable_btn.setText("已暂停 · 点击启用")
            self.enable_btn.setObjectName("ghost")
        self.enable_btn.style().unpolish(self.enable_btn)
        self.enable_btn.style().polish(self.enable_btn)

    def _save(self) -> None:
        path = self.cfg.save()
        self.msg.setText(f"已保存 · {path.name}")
        QTimer.singleShot(2600, lambda: self.msg.setText(""))

    # ================================================================ 刷新
    def _tick(self) -> None:
        if not self.isVisible():
            return

        snap = self.app.detector.snapshot()

        if not self.cfg["enabled"]:
            if self.cfg["schedule_enabled"] and not in_window(self.cfg):
                self._set_badge("计划外暂停", SUB)
            else:
                self._set_badge("已暂停", SUB)
            active_state = STATE_PAUSED
        elif not snap.ready:
            self._set_badge("启动中", SUB)
            active_state = STATE_PAUSED
        elif snap.calibrating:
            self._set_badge(f"校准中 {snap.calib_progress * 100:.0f}%", SUB)
            active_state = STATE_PRESENT
        elif snap.state == STATE_PRESENT:
            self._set_badge("在位", OK)
            active_state = STATE_PRESENT
        elif snap.state == STATE_AWAY:
            self._set_badge("已离开", WARN)
            active_state = STATE_AWAY
        else:
            self._set_badge("待机", SUB)
            active_state = STATE_PAUSED

        extra = f"  ·  {snap.away_for:.1f}s" if active_state == STATE_AWAY else ""

        self._refresh_enable_btn()
        self._refresh_schedule_status()
        self._refresh_pay_preview()

        if self.cfg["calibrated"]:
            self.cal_label.setText("已校准 · 当前坐姿已设为基准")
        else:
            self.cal_label.setText("使用默认基准 · 正对屏幕时角度接近 0° 就不用校准")
        self.cal_banner.setVisible(not self.cfg["calibrated"])

        guard = (
            f"键鼠活动中（{snap.idle:.0f}s 前有输入）" if snap.active
            else f"键鼠空闲 {snap.idle:.0f}s"
        )
        if snap.face_found and not snap.pose_ok:
            pose_line = "朝向：读不出（侧脸 / 遮挡 / 逆光）"
        elif snap.face_found:
            pose_line = (
                f"yaw {snap.yaw:+6.1f}°     pitch {snap.pitch:+6.1f}°     "
                f"roll {snap.roll:+6.1f}°"
            )
        else:
            pose_line = "朝向：画面里没有人脸"

        self.metrics.setText(
            f"{pose_line}\n"
            f"人脸占比 {snap.face_ratio * 100:4.1f}%     偏离中心 {snap.center_dist:.2f}"
            f"     {guard}     帧率 {snap.fps:4.1f} fps{extra}"
        )

        if snap.calibrating and active_state != STATE_AWAY:
            self.reasons.setText("正在学习你的基准姿态，请正对屏幕不要转头…")
            self.reasons.setStyleSheet(f"color: {SUB}; font-size: 12px;")
        elif active_state == STATE_AWAY and snap.reasons:
            self.reasons.setText("触发原因：" + "、".join(snap.reasons))
            self.reasons.setStyleSheet(f"color: {WARN}; font-size: 12px;")
        elif active_state == STATE_PRESENT and snap.face_found:
            note = "（键鼠活动中，已忽略上一次视觉误判）" if snap.guard_blocked else ""
            self.reasons.setText("判定：在位" + note)
            self.reasons.setStyleSheet(f"color: {OK}; font-size: 12px;")
        elif active_state == STATE_PAUSED and snap.ready and snap.face_found:
            self.reasons.setText("判定：" + ("、".join(snap.reasons) if snap.reasons else "停在当前画面"))
            self.reasons.setStyleSheet(f"color: {SUB}; font-size: 12px;")
        else:
            self.reasons.setText("")
            self.reasons.setStyleSheet("")

        if self._preview_on:
            frame = self.app.detector.latest_frame()
            if frame is not None:
                self._render(frame)

    def _refresh_schedule_status(self) -> None:
        label = getattr(self, "schedule_status", None)
        if label is None:
            return

        text = describe_now(self.cfg)
        if label.text() != text:
            label.setText(text)

        if not self.cfg["schedule_enabled"]:
            color = SUB
        elif in_window(self.cfg):
            color = OK
        else:
            color = WARN
        if getattr(self, "_sched_color", None) != color:
            self._sched_color = color
            label.setStyleSheet(
                f"color: {color}; font-size: 13px; font-weight: 600;"
            )

    def _set_badge(self, text: str, color: str) -> None:
        if self.badge.text() != text:
            self.badge.setText(text)
            self.badge.setStyleSheet(
                f"background: {color}1f; color: {color}; border-radius: 11px;"
                f" padding: 4px 14px; font-weight: 600; font-size: 12px;"
            )

    def _render(self, frame) -> None:
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
            pix = QPixmap.fromImage(img)
            avail_w = max(200, self.video.width() - 16)
            avail_h = max(150, self.video.height() - 16)
            pix = pix.scaled(avail_w, avail_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.video.setPixmap(pix)
            self.video.setText("")
        except Exception:
            pass

    # ================================================================ 窗口
    def closeEvent(self, event) -> None:  # noqa: N802
        if self._allow_close:
            event.accept()
            return
        event.ignore()
        self.cfg["show_preview"] = False
        self._preview_on = False
        self.preview_check.setChecked(False)
        self.hide()

    def force_close(self) -> None:
        self._allow_close = True
        self.close()

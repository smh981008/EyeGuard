# -*- coding: utf-8 -*-
"""界面配色与 QSS 样式表。"""

TEXT = "#1f2329"
SUB = "#6b7280"
ACCENT = "#2f6fed"
OK = "#12b76a"
WARN = "#f04438"
BG = "#f4f5f7"

QSS = """
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: 13px;
    color: #1f2329;
}

QWidget#root { background: #f4f5f7; }

QLabel#title { font-size: 21px; font-weight: 600; color: #111827; }
QLabel#subtitle { font-size: 12px; color: #9099a8; }
QLabel#section { font-size: 12px; font-weight: 600; color: #8b93a1; }
QLabel#hint { font-size: 12px; color: #9099a8; }
QLabel#value { font-size: 13px; font-weight: 600; color: #2f6fed; }
QLabel#metric { font-family: "Consolas", monospace; font-size: 12px; color: #6b7280; }

QFrame#card {
    background: #ffffff;
    border: 1px solid #eaecf0;
    border-radius: 12px;
}

QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab {
    background: transparent; color: #6b7280;
    padding: 8px 18px; margin-right: 4px;
    border-radius: 8px; font-size: 13px;
}
QTabBar::tab:selected { background: #ffffff; color: #2f6fed; font-weight: 600; }
QTabBar::tab:hover:!selected { color: #374151; background: #f7f9fc; }

/* 配置方案列表：两行一项，选中要有明确底色，否则看不出保存目标是哪个 */
QListWidget {
    background: #fbfcfd; border: 1px solid #eaecf0; border-radius: 10px;
    padding: 4px; font-size: 12px; outline: none;
}
QListWidget::item { padding: 5px 10px; border-radius: 7px; color: #1f2329; }
QListWidget::item:hover:!selected { background: #f2f5fa; }
QListWidget::item:selected { background: #e8f0fe; color: #1f4fc4; }

QCheckBox { spacing: 9px; font-size: 13px; color: #1f2329; }
QCheckBox::indicator { width: 17px; height: 17px; border-radius: 5px;
                       border: 1px solid #d1d5db; background: #ffffff; }
QCheckBox::indicator:hover { border-color: #2f6fed; }
QCheckBox::indicator:checked { background: #2f6fed; border-color: #2f6fed; }
QCheckBox::indicator:checked:hover { background: #1f56c7; border-color: #1f56c7; }
QCheckBox:disabled { color: #b0b7c3; }

QSlider::groove:horizontal { height: 4px; background: #e8eaee; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #2f6fed; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 14px; height: 14px; margin: -6px 0;
    border-radius: 8px; background: #ffffff; border: 3px solid #2f6fed;
}
QSlider::handle:horizontal:hover { border-color: #1f56c7; }

QPushButton#primary {
    background: #2f6fed; color: #ffffff; border: none;
    border-radius: 8px; padding: 8px 18px; font-weight: 600;
}
QPushButton#primary:hover { background: #1f56c7; }
QPushButton#primary:pressed { background: #1a4aa8; }

QPushButton#ghost {
    background: #ffffff; color: #374151; border: 1px solid #d8dce3;
    border-radius: 8px; padding: 8px 16px;
}
QPushButton#ghost:hover { background: #f7f8fa; border-color: #c3c9d4; }
QPushButton#ghost:pressed { background: #eef0f4; }

QPushButton#success {
    background: #12b76a; color: #ffffff; border: none;
    border-radius: 8px; padding: 8px 18px; font-weight: 600;
}
QPushButton#success:hover { background: #0e9f5e; }

QLineEdit {
    background: #ffffff; border: 1px solid #d8dce3; border-radius: 8px;
    padding: 7px 10px; selection-background-color: #2f6fed;
}
QLineEdit:focus { border-color: #2f6fed; }

QTimeEdit {
    background: #ffffff; border: 1px solid #d8dce3; border-radius: 8px;
    padding: 6px 8px; min-width: 78px;
    font-family: "Consolas", "Microsoft YaHei UI", monospace; font-size: 13px;
}
QTimeEdit:hover { border-color: #c3c9d4; }
QTimeEdit:focus { border-color: #2f6fed; }
QTimeEdit:disabled { color: #b0b7c3; background: #f7f8fa; }
/* 箭头按钮压窄并透明化。注意不能设 NoButtons：自定义样式下 Qt 会把
   编辑区宽度算成 0，时间文字整块不渲染。 */
QTimeEdit::up-button, QTimeEdit::down-button {
    width: 15px; background: transparent; border: none;
}

QPushButton#day {
    background: #ffffff; color: #6b7280;
    border: 1px solid #d8dce3; border-radius: 8px;
    font-size: 13px; padding: 0;
}
QPushButton#day:hover:!checked { border-color: #b9c0cc; color: #374151; }
QPushButton#day:checked {
    background: #2f6fed; color: #ffffff; border-color: #2f6fed; font-weight: 600;
}
QPushButton#day:checked:hover { background: #1f56c7; border-color: #1f56c7; }

QComboBox {
    background: #ffffff; border: 1px solid #d8dce3; border-radius: 8px;
    padding: 6px 10px; min-width: 90px;
}
QComboBox:hover { border-color: #c3c9d4; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: #ffffff; border: 1px solid #d8dce3;
    selection-background-color: #eef4ff; selection-color: #1f2329;
    outline: none;
}

QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #d5d9e0; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #bcc2cc; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QMenu {
    background: #ffffff; border: 1px solid #e5e7eb;
    border-radius: 8px; padding: 6px;
}
QMenu::item { padding: 7px 26px 7px 12px; border-radius: 6px; color: #1f2329; }
QMenu::item:selected { background: #eef4ff; color: #2f6fed; }
QMenu::separator { height: 1px; background: #eef0f4; margin: 4px 8px; }

QToolTip {
    background: #1f2329; color: #ffffff; border: none;
    border-radius: 6px; padding: 5px 8px;
}
"""

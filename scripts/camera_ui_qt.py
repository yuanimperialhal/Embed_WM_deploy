"""Native Qt presentation; camera capture and model work never block the UI."""
import copy
from datetime import datetime
import glob
import json
import multiprocessing
import queue
import threading
import time

from PyQt5.QtCore import Qt, QTimer, QRectF, QPointF, QSize, QUrl
from PyQt5.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QTextCursor, QDesktopServices
from PyQt5.QtWidgets import (QApplication, QComboBox, QDialog, QFrame, QHBoxLayout,
    QLabel, QMenu, QPlainTextEdit, QPushButton, QSizePolicy, QStackedWidget,
    QTextEdit, QVBoxLayout, QWidget, QLineEdit, QFileDialog)
from camera_infer import ROOT, LatestFrame, encode_frame, parse_args
from camera_ui_backend import ModelService, inference_worker
from app_runtime import (VERSION, DEFAULT_PROMPT, default_settings, save_settings,
                         settings_path, logs_dir, model_files_error)

STYLE = '''
QWidget { color: #1d1d1f; font-family: "Noto Sans CJK SC"; font-size: 13px; }
QWidget#workspace { background: #f5f5f7; }
QFrame#card { background: #ffffff; border: 1px solid #e5e5ea; border-radius: 20px; }
QFrame#composer { background: #f7f8fa; border: 1px solid #e9eaee; border-radius: 14px; }
QFrame#sample { background: #f7f8fa; border: none; border-radius: 12px; }
QLabel { background: transparent; border: none; }
QLineEdit { background: white; border: 1px solid #dfe2e8; border-radius: 8px; padding: 9px; }
QLineEdit:focus { border: 1px solid #007aff; }
QLabel#brand { font-size: 21px; font-weight: 600; letter-spacing: -0.6px; }
QLabel#pageTitle { font-size: 27px; font-weight: 600; letter-spacing: -0.5px; }
QLabel#sectionTitle { font-size: 16px; font-weight: 600; }
QLabel#emptyTitle { font-size: 19px; font-weight: 500; }
QLabel#subtitle, QLabel#muted { color: #777b84; font-size: 12px; }
QLabel#eyebrow { color: #777b84; font-size: 11px; letter-spacing: 1.5px; }
QLabel#status { color: #737780; font-size: 11px; }
QLabel#badge { color: #686d77; background: #e9eaee; border-radius: 13px; padding: 5px 12px; font-size: 11px; }
QPushButton { background: #f0f1f4; border: 1px solid transparent; border-radius: 10px; padding: 8px 14px; color: #454a54; }
QPushButton:hover { background: #e6e8ed; }
QPushButton:pressed { background: #dce0e8; }
QPushButton:focus, QComboBox:focus { border: 1px solid #007aff; }
QPushButton:disabled { color: #a5a8af; background: #f2f3f5; }
QPushButton#primary { background: #007aff; color: white; font-weight: 500; border-radius: 11px; padding: 11px 16px; }
QPushButton#primary:hover { background: #006be0; }
QPushButton#primary:pressed { background: #005ec5; }
QPushButton#primary:disabled { background: #c9dffc; color: #f5f9ff; }
QPushButton#quiet { background: transparent; color: #747985; padding: 7px 9px; }
QPushButton#quiet:hover { background: #eceef2; color: #1d1d1f; }
QPushButton#chip { background: #f1f2f5; border: 1px solid transparent; border-radius: 8px; font-size: 11px; padding: 6px 9px; }
QPushButton#chip:checked { background: #e8f1ff; color: #0069df; }
QPushButton#chip:hover { background: #e5ebf4; }
QPushButton#model { background: #fff; border: 1px solid #e5e5ea; border-radius: 16px; padding: 7px 12px; font-size: 11px; color: #696f79; }
QPushButton#model:hover { background: #f0f2f5; }
QComboBox { background: #f5f6f8; border: 1px solid transparent; border-radius: 8px; padding: 6px 10px; color: #717682; font-size: 11px; min-width: 102px; }
QComboBox:disabled { color: #858a94; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: white; selection-background-color: #e8f1ff; selection-color: #007aff; border: 1px solid #e5e5ea; padding: 4px; }
QPlainTextEdit, QTextEdit { border: none; background: transparent; selection-background-color: #d2e5ff; selection-color: #1d1d1f; }
QPlainTextEdit#prompt { font-size: 13px; padding: 0; }
QTextEdit#answer { font-size: 16px; color: #2c3038; }
QScrollBar:vertical { background: transparent; width: 5px; margin: 2px 0; }
QScrollBar::handle:vertical { background: #d3d6dd; border-radius: 2px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QMenu { background: #ffffff; border: 1px solid #e1e3e8; border-radius: 10px; padding: 7px; }
QMenu::item { padding: 9px 20px; border-radius: 6px; }
QMenu::item:selected { background: #eaf2ff; color: #006add; }
QMenu::item:disabled { color: #a5a8af; }
QMenu::separator { height: 1px; background: #eeeff2; margin: 5px 8px; }
QToolTip { background: #fff; color: #454a54; border: 1px solid #dedfe5; padding: 6px; }
QDialog { background: #f5f5f7; }
'''


STYLE += 'QComboBox::down-arrow { image: url(' + str(ROOT/'assets/chevron-down.svg') + '); width: 10px; height: 10px; }'


def icon(name, color='#727985', size=20):
    """Small original line icons, rendered at 2× for high density displays."""
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 12, size / 12)
    p.setPen(QPen(QColor(color), 1.55, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if name == 'camera':
        p.drawRoundedRect(QRectF(3, 6, 18, 14), 3, 3)
        p.drawEllipse(QPointF(12, 13), 3.5, 3.5)
        p.drawLine(QPointF(8, 6), QPointF(9, 3.5))
        p.drawLine(QPointF(9, 3.5), QPointF(15, 3.5))
        p.drawLine(QPointF(15, 3.5), QPointF(16, 6))
    elif name == 'spark':
        path = QPainterPath(QPointF(12, 3))
        for xy in [(14.5, 9.5), (21, 12), (14.5, 14.5), (12, 21), (9.5, 14.5), (3, 12), (9.5, 9.5)]:
            path.lineTo(*xy)
        path.closeSubpath()
        p.drawPath(path)
    elif name == 'play':
        path = QPainterPath(QPointF(8, 5))
        path.lineTo(19, 12)
        path.lineTo(8, 19)
        path.closeSubpath()
        p.setBrush(QColor(color))
        p.drawPath(path)
    elif name == 'pause':
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(6, 5, 3, 14), 1, 1)
        p.drawRoundedRect(QRectF(15, 5, 3, 14), 1, 1)
    elif name == 'history':
        p.drawArc(QRectF(4, 4, 16, 16), -50 * 16, 295 * 16)
        p.drawLine(QPointF(3, 4), QPointF(3, 9))
        p.drawLine(QPointF(3, 9), QPointF(8, 9))
        p.drawLine(QPointF(12, 7), QPointF(12, 12))
        p.drawLine(QPointF(12, 12), QPointF(16, 14))
    elif name == 'more':
        p.setBrush(QColor(color))
        for x in [5, 12, 19]:
            p.drawEllipse(QPointF(x, 12), 1, 1)
    elif name == 'expand':
        for x, y, sx, sy in [(4, 4, 1, 1), (20, 4, -1, 1), (4, 20, 1, -1), (20, 20, -1, -1)]:
            p.drawLine(QPointF(x, y), QPointF(x + sx * 5, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + sy * 5))
    elif name == 'dot':
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QPointF(12, 12), 4, 4)
    p.end()
    pix.setDevicePixelRatio(2)
    return QIcon(pix)


def configure_application(app):
    app.setStyle('Fusion')
    app.setFont(QFont('Noto Sans CJK SC', 10))
    app.setApplicationName('Cosmos 相机助手')
    app.setApplicationVersion(VERSION)
    app.setDesktopFileName('cosmos-camera')
    app.setWindowIcon(QIcon(str(ROOT/'assets/cosmos-camera.svg')))
    app.setStyleSheet(STYLE)


def label(text='', name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    if name:
        widget.setObjectName(name)
    return widget


def box(widget, direction='v', margins=0, spacing=0):
    layout = QVBoxLayout(widget) if direction == 'v' else QHBoxLayout(widget)
    layout.setContentsMargins(*([margins] * 4 if isinstance(margins, int) else margins))
    layout.setSpacing(spacing)
    return layout


def button(text, callback, name=None, glyph=None):
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    if glyph:
        b.setIcon(icon(glyph))
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(callback)
    return b


class VideoView(QWidget):
    """Aspect-preserving viewport with a rounded clip; no per-frame effects."""
    def __init__(self, compact=False, parent=None):
        super().__init__(parent)
        self.image = None
        self.compact = compact
        self.message = '相机未打开'
        self.detail = '点击右上角的相机按钮开始预览'
        self.setMinimumSize(64, 48) if compact else self.setMinimumSize(280, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_frame(self, image):
        self.image = image
        self.update()

    def clear(self, message='相机已关闭', detail='点击右上角的相机按钮重新连接'):
        self.image = None
        self.message, self.detail = message, detail
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, 9 if self.compact else 14, 9 if self.compact else 14)
        p.setClipPath(path)
        p.fillRect(rect, QColor('#eceef2' if self.compact else '#14171c'))
        if self.image is not None:
            scaled = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
            target = QRectF((self.width() - scaled.width()) / 2,
                            (self.height() - scaled.height()) / 2, scaled.width(), scaled.height())
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(target, self.image)
        elif not self.compact:
            cx, cy = self.width() / 2, self.height() / 2 - 28
            icon('camera', '#646c7b', 36).paint(p, int(cx-18), int(cy-40), 36, 36)
            p.setPen(QColor('#b7beca'))
            p.setFont(QFont('Noto Sans CJK SC', 11))
            p.drawText(QRectF(12, cy + 8, self.width()-24, 30), Qt.AlignCenter, self.message)
            p.setPen(QColor('#79818e'))
            p.setFont(QFont('Noto Sans CJK SC', 9))
            p.drawText(QRectF(12, cy + 42, self.width()-24, 30), Qt.AlignCenter, self.detail)
        p.end()


class CameraApp(QWidget):
    def __init__(self, source=None, auto_camera=True, settings=None):
        super().__init__()
        import cv2
        self.cv2 = cv2
        self.settings = dict(settings or default_settings())
        if source is not None:
            self.settings['camera_source'] = source
        self.args = parse_args(['--source', self.settings['camera_source']])
        self.events = queue.Queue()
        self.camera = None
        self.camera_closing = False
        self.worker = self.connection = None
        self.worker_ready = False
        self.pending = self.running = self.once = self.closing = self.cleaned = False
        self.model_state = 'checking'
        self.shown = self.last_sent = self.completed = self.preview_updates = 0
        self.next_start = self.request_start = 0.0
        self.last_result = None
        self.history = []
        self.frame_received = None
        self.preview_window = None
        self.dialogs = []
        self.run_dir = logs_dir()/datetime.now().strftime('camera-ui-%Y%m%d-%H%M%S-%f')
        self.run_dir.mkdir(parents=True)
        self.log = (self.run_dir/'results.jsonl').open('a', encoding='utf-8')
        self.service = ModelService(self.events, self.run_dir/'server.log', self.settings['model_dir'])
        self.diagnostics_busy = False
        self.setWindowTitle('Cosmos 相机助手')
        self.setObjectName('workspace')
        self.resize(1220, 820)
        self.setMinimumSize(1000, 720)
        self._build()
        self.service.start(launch=False)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(80)
        if auto_camera:
            QTimer.singleShot(250, self.open_camera)

    def _build(self):
        outer = box(self, margins=(28, 18, 28, 14), spacing=0)
        header = QHBoxLayout()
        brand_icon = label()
        brand_icon.setPixmap(icon('spark', '#007aff', 27).pixmap(27, 27))
        header.addWidget(brand_icon)
        header.addSpacing(8)
        header.addWidget(label('Cosmos', 'brand'))
        header.addSpacing(16)
        header.addWidget(label('相机助手', 'muted'))
        header.addStretch()
        self.model_button = button('正在检查模型', self.show_model_menu, 'model', 'dot')
        self.model_button.setToolTip('查看模型状态和管理服务')
        header.addWidget(self.model_button)
        header.addSpacing(9)
        self.history_button = button('', self.show_history, 'quiet', 'history')
        self.history_button.setFixedSize(34, 34)
        self.history_button.setToolTip('最近的分析')
        self.history_button.setAccessibleName('历史结果')
        header.addWidget(self.history_button)
        more = button('', self.show_more_menu, 'quiet', 'more')
        more.setFixedSize(34, 34)
        more.setToolTip('更多选项')
        more.setAccessibleName('更多选项')
        self.more_button = more
        header.addWidget(more)
        outer.addLayout(header)
        outer.addSpacing(26)
        title_row = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(5)
        titles.addWidget(label('看见，即可理解。', 'pageTitle'))
        titles.addWidget(label('实时观察眼前的世界，让 Cosmos 为你解读。', 'subtitle'))
        title_row.addLayout(titles)
        title_row.addStretch()
        badge = label('本地运行 · Jetson', 'badge')
        title_row.addWidget(badge, 0, Qt.AlignBottom)
        outer.addLayout(title_row)
        outer.addSpacing(24)
        body = QHBoxLayout()
        body.setSpacing(18)
        self.camera_card = QFrame()
        self.camera_card.setObjectName('card')
        left = box(self.camera_card, margins=18, spacing=14)
        bar = QHBoxLayout()
        bar.addWidget(label('实时画面', 'sectionTitle'))
        bar.addStretch()
        self.device_combo = QComboBox()
        self.device_combo.setAccessibleName('相机设备')
        self.device_combo.setToolTip('关闭相机后可切换设备')
        devices = sorted(set(glob.glob('/dev/video*') + [self.args.source]))
        self.device_combo.addItems(devices)
        self.device_combo.setCurrentText(self.args.source)
        bar.addWidget(self.device_combo)
        self.camera_button = button('', self.toggle_camera, 'quiet', 'camera')
        self.camera_button.setFixedSize(34, 34)
        self.camera_button.setToolTip('打开相机')
        self.camera_button.setAccessibleName('打开或关闭相机')
        bar.addWidget(self.camera_button)
        expand = button('', self.expand_preview, 'quiet', 'expand')
        expand.setFixedSize(34, 34)
        expand.setToolTip('放大实时画面')
        expand.setAccessibleName('放大实时画面')
        bar.addWidget(expand)
        left.addLayout(bar)
        self.video = VideoView()
        left.addWidget(self.video, 1)
        camera_meta = QHBoxLayout()
        self.camera_label = label('○  等待相机', 'muted')
        camera_meta.addWidget(self.camera_label)
        camera_meta.addStretch()
        self.resolution = label('USB CAMERA', 'eyebrow')
        camera_meta.addWidget(self.resolution)
        left.addLayout(camera_meta)
        body.addWidget(self.camera_card, 6)

        self.analysis_card = QFrame()
        self.analysis_card.setObjectName('card')
        self.analysis_card.setMinimumWidth(360)
        right = box(self.analysis_card, margins=20, spacing=0)
        bar = QHBoxLayout()
        mark = label()
        mark.setPixmap(icon('spark', '#007aff', 19).pixmap(19, 19))
        bar.addWidget(mark)
        bar.addSpacing(5)
        bar.addWidget(label('画面解读', 'sectionTitle'))
        bar.addStretch()
        self.analysis_state = label('等待开始', 'muted')
        bar.addWidget(self.analysis_state)
        right.addLayout(bar)
        right.addSpacing(16)
        self.result_stack = QStackedWidget()
        empty = QWidget()
        empty_layout = box(empty, margins=(5, 12, 5, 18), spacing=10)
        empty_layout.addStretch()
        empty_icon = label()
        empty_icon.setAlignment(Qt.AlignCenter)
        empty_icon.setPixmap(icon('spark', '#c0cee2', 44).pixmap(44, 44))
        empty_layout.addWidget(empty_icon)
        title = label('从眼前这一刻开始', 'emptyTitle')
        title.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(title)
        detail = label('写下想了解的内容，点击开始分析。\n回答会在这里逐步呈现。', 'muted')
        detail.setAlignment(Qt.AlignCenter)
        detail.setWordWrap(True)
        empty_layout.addWidget(detail)
        empty_layout.addStretch()
        self.result_stack.addWidget(empty)
        result = QWidget()
        result_layout = box(result, spacing=12)
        sample = QFrame()
        sample.setObjectName('sample')
        sample_layout = box(sample, 'h', margins=10, spacing=12)
        self.sample = VideoView(compact=True)
        self.sample.setFixedSize(64, 48)
        sample_layout.addWidget(self.sample)
        sample_text = QVBoxLayout()
        sample_text.setSpacing(2)
        self.sample_info = label('本次分析的画面')
        self.sample_age = label('', 'muted')
        sample_text.addWidget(self.sample_info)
        sample_text.addWidget(self.sample_age)
        sample_layout.addLayout(sample_text, 1)
        result_layout.addWidget(sample)
        self.answer = QTextEdit()
        self.answer.setObjectName('answer')
        self.answer.setReadOnly(True)
        self.answer.setAcceptRichText(False)
        self.answer.setMinimumHeight(70)
        self.answer.document().setDocumentMargin(1)
        result_layout.addWidget(self.answer, 1)
        self.metrics = label('', 'muted')
        result_layout.addWidget(self.metrics)
        self.result_stack.addWidget(result)
        right.addWidget(self.result_stack, 1)
        right.addSpacing(16)
        composer = QFrame()
        composer.setObjectName('composer')
        compose = box(composer, margins=14, spacing=10)
        compose.addWidget(label('想了解什么？', 'muted'))
        self.prompt = QPlainTextEdit()
        self.prompt.setObjectName('prompt')
        self.prompt.setAccessibleName('分析问题')
        self.prompt.setPlaceholderText('例如：桌面上有哪些物体？')
        self.prompt.setFixedHeight(74)
        self.prompt.setTabChangesFocus(True)
        compose.addWidget(self.prompt)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.presets = []
        for title, prompt in (
            ('描述场景', DEFAULT_PROMPT),
            ('有没有人', '画面中有没有人？如果有，简短描述其位置和可见姿态。看不清就说不确定。'),
            ('物体位置', '用一两句话描述画面中的主要物体及相对位置。看不清就说不确定。'),
        ):
            chip = button(title, lambda checked=False, p=prompt: self.set_prompt(p), 'chip')
            chip.setCheckable(True)
            chips.addWidget(chip)
            self.presets.append((chip, prompt))
        chips.addStretch()
        compose.addLayout(chips)
        self.prompt.textChanged.connect(self.sync_presets)
        self.set_prompt(self.settings['prompt'])
        right.addWidget(composer)
        right.addSpacing(14)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.run_button = button('开始连续分析', self.toggle_analysis, 'primary')
        self.run_button.setIcon(icon('play', '#ffffff', 16))
        self.run_button.setMinimumHeight(42)
        actions.addWidget(self.run_button, 1)
        self.once_button = button('分析一次', lambda: self.start_analysis(once=True))
        self.once_button.setMinimumHeight(42)
        actions.addWidget(self.once_button)
        right.addLayout(actions)
        right.addSpacing(10)
        note = label('结果对应取样画面 · 修改问题将在下一次生效', 'status')
        note.setAlignment(Qt.AlignCenter)
        note.setWordWrap(True)
        right.addWidget(note)
        body.addWidget(self.analysis_card, 4)
        outer.addLayout(body, 1)
        outer.addSpacing(12)
        footer = QHBoxLayout()
        self.status = label('正在准备相机…', 'status')
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(28)
        footer.addWidget(self.status, 1)
        footer.addSpacing(18)
        footer.addWidget(label('COSMOS  /  VISION', 'eyebrow'))
        outer.addLayout(footer)

    def set_status(self, text):
        self.status.setText(text)
        self.status.setToolTip(text)

    def set_answer(self, text, append=False):
        self.result_stack.setCurrentIndex(1)
        if append:
            cursor = self.answer.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertText(text)
            self.answer.setTextCursor(cursor)
        else:
            self.answer.setPlainText(text)
        self.answer.ensureCursorVisible()

    def set_prompt(self, text):
        self.prompt.setPlainText(text)

    def sync_presets(self):
        text = self.prompt.toPlainText().strip()
        for chip, prompt in self.presets:
            chip.setChecked(text == prompt)

    def show_model_menu(self):
        menu = QMenu(self)
        owned = self.service.process is not None and self.service.process.poll() is None
        if self.model_state == 'ready' and not owned:
            action = menu.addAction('正在使用已有的模型服务')
            action.setEnabled(False)
        else:
            title = '取消加载' if self.model_state == 'loading' else '关闭模型，释放内存' if owned else '启动模型'
            action = menu.addAction(title, self.toggle_model)
            action.setEnabled(self.model_state not in ('checking', 'stopping'))
        menu.addSeparator()
        menu.addAction('查看服务日志', self.show_server_log)
        menu.exec_(self.model_button.mapToGlobal(self.model_button.rect().bottomLeft()))

    def show_more_menu(self):
        menu = QMenu(self)
        menu.addAction('设置', self.show_settings)
        menu.addAction('准备 / 修复运行环境', self.show_setup)
        check = menu.addAction('检查运行环境', self.run_diagnostics)
        check.setEnabled(not self.diagnostics_busy)
        menu.addAction('打开日志文件夹', self.open_logs)
        menu.addSeparator()
        menu.addAction('查看使用说明', self.show_guide)
        menu.addAction('查看服务日志', self.show_server_log)
        menu.addAction('关于相机助手', self.show_about)
        menu.addSeparator()
        menu.addAction('退出相机助手', self.close)
        menu.exec_(self.more_button.mapToGlobal(self.more_button.rect().bottomRight()))

    def show_setup(self):
        if self.running or self.pending or self.service.busy or self.model_state in ('ready', 'loading', 'stopping'):
            self.set_status('请先暂停分析并关闭本窗口的模型服务，再准备运行环境。')
            return
        from setup_ui import SetupDialog
        from app_runtime import load_settings
        dialog = SetupDialog(self)
        if dialog.exec_() == dialog.Accepted:
            self.settings = load_settings()
            self.service.model_dir = self.settings['model_dir']
            self.set_status('运行环境已准备完成，可以开始分析。')

    def show_settings(self):
        if self.running or self.pending or self.service.busy or self.model_state == 'stopping':
            self.set_status('请先暂停分析，并等待模型加载或关闭结束，再修改设置。')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('设置 · Cosmos 相机助手')
        dialog.resize(680, 350)
        layout = box(dialog, margins=24, spacing=14)
        layout.addWidget(label('设置', 'sectionTitle'))
        layout.addWidget(label('选择模型目录', 'muted'))
        row = QHBoxLayout()
        field = QLineEdit(self.settings['model_dir'])
        field.setAccessibleName('模型目录')
        row.addWidget(field, 1)
        def browse():
            path = QFileDialog.getExistingDirectory(dialog, '选择完整的 Cosmos 模型目录', field.text())
            if path:
                field.setText(path)
        row.addWidget(button('选择文件夹', browse))
        layout.addLayout(row)
        tip = label('请选择包含 model.safetensors 和 tokenizer.json 等文件的文件夹。\n保存时也会记住当前相机设备和输入的问题。', 'muted')
        tip.setWordWrap(True)
        layout.addWidget(tip)
        error_label = label('', 'muted')
        error_label.setWordWrap(True)
        error_label.setStyleSheet('color: #ba4e40;')
        layout.addWidget(error_label)
        layout.addStretch()
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button('取消', dialog.reject))
        def save():
            try:
                updated = dict(self.settings, model_dir=field.text().strip(),
                               camera_source=self.device_combo.currentText(),
                               prompt=self.prompt.toPlainText().strip())
                if updated['model_dir'] != self.settings['model_dir'] and self.model_state == 'ready':
                    raise ValueError('请先关闭模型服务，再更换模型目录。')
                from app_runtime import validate_settings
                updated = validate_settings(updated)
                error = model_files_error(updated['model_dir'])
                if error:
                    raise ValueError(error)
                self.settings = save_settings(updated)
                self.service.model_dir = self.settings['model_dir']
                self.set_status('设置已保存，下次启动会恢复当前相机和问题。')
                dialog.accept()
            except (OSError, ValueError) as exc:
                error_label.setText(str(exc))
        actions.addWidget(button('保存设置', save, 'primary'))
        layout.addLayout(actions)
        dialog.exec_()

    def run_diagnostics(self):
        if self.diagnostics_busy or self.closing:
            return
        self.diagnostics_busy = True
        self.set_status('正在检查模型文件、相机权限和推理环境…')
        model_dir = self.settings['model_dir']
        def check():
            from app_diagnostics import check_environment, format_report
            try:
                report = format_report(check_environment(model_dir))
            except Exception as exc:
                report = '检查未能完成：' + str(exc)
            self.events.put(('diagnostics', report))
        threading.Thread(target=check, daemon=True).start()

    def open_logs(self):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir()))):
            self.text_window('日志位置', str(logs_dir()))

    def show_about(self):
        self.text_window('关于 Cosmos 相机助手',
            f'Cosmos 相机助手 {VERSION}\n\nJetson Linux · USB 相机实时预览与单帧视觉分析\n'
            f'\n配置文件：{settings_path()}\n日志目录：{logs_dir()}\n'
            '\n首个可安装预览版本。模型权重、JetPack 和 NVIDIA 推理容器单独准备。')

    def open_camera(self):
        if self.camera is not None or self.camera_closing or self.closing:
            return
        self.args.source = self.device_combo.currentText()
        self.camera = LatestFrame(self.cv2, self.args)
        self.camera.start()
        self.camera_opened = time.perf_counter()
        self.shown = self.last_sent = 0
        self.device_combo.setEnabled(False)
        self.camera_button.setToolTip('关闭相机')
        self.camera_button.setIcon(icon('camera', '#007aff'))
        self.video.clear('正在连接相机', '画面就绪后会自动显示')
        self.set_status('正在连接 USB 相机…')

    def toggle_camera(self):
        self.open_camera() if self.camera is None else self.stop_camera()

    def stop_camera(self):
        self.stop_analysis()
        camera, self.camera = self.camera, None
        self.camera_label.setText('○  相机已关闭')
        self.camera_label.setStyleSheet('')
        self.video.clear()
        if self.preview_window is not None:
            self.preview_window.video.clear()
        self.camera_button.setToolTip('打开相机')
        self.camera_button.setIcon(icon('camera'))
        self.camera_button.setEnabled(False)
        if camera is not None:
            self.camera_closing = True
            def release():
                camera.close()
                camera.thread.join()
                self.events.put(('camera_closed', None))
            threading.Thread(target=release, daemon=True).start()
        else:
            self.camera_button.setEnabled(True)
            self.device_combo.setEnabled(True)
        self.set_status('相机已关闭，已保留本次分析结果。')

    def toggle_model(self):
        if self.model_state == 'loading' or (self.service.process is not None and self.service.process.poll() is None):
            self.stop_analysis()
            self.model_state = 'stopping'
            self.model_button.setText('正在关闭模型')
            threading.Thread(target=self._stop_model, daemon=True).start()
        elif self.model_state != 'ready':
            self.service.start()

    def _stop_model(self):
        try:
            self.service.stop()
            self.events.put(('model', 'offline'))
        except Exception as exc:
            self.events.put(('model_error', f'关闭模型失败：{exc}'))

    def start_analysis(self, once=False):
        if self.running or self.pending or self.closing or self.model_state == 'stopping':
            return
        prompt = self.prompt.toPlainText().strip()
        if not prompt or len(prompt) > 400:
            self.set_status('请填写简短问题（1–400 个字符）。')
            self.prompt.setFocus()
            return
        self.open_camera()
        if self.camera is None:
            self.set_status('相机正在关闭，稍后再试。')
            return
        self.running, self.once = True, once
        self.run_button.setText('停止分析' if once else '暂停分析')
        self.run_button.setIcon(icon('pause', '#ffffff', 16))
        self.once_button.setEnabled(False)
        if self.model_state != 'ready':
            self.analysis_state.setText('准备模型')
            self.set_status('首次加载需要几分钟，就绪后自动开始。相机画面会继续刷新。')
            self.service.start()
        else:
            self.analysis_state.setText('准备分析')
            self.set_status('正在选取最新画面…')

    def toggle_analysis(self):
        self.stop_analysis() if self.running or self.pending else self.start_analysis()

    def stop_analysis(self):
        was_pending = self.pending
        self.running = self.pending = False
        self.worker_ready = False
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.worker is not None:
            worker, self.worker = self.worker, None
            if worker.is_alive():
                worker.terminate()
            threading.Thread(target=worker.join, daemon=True).start()
        self.run_button.setText('开始连续分析')
        self.run_button.setIcon(icon('play', '#ffffff', 16))
        self.once_button.setEnabled(True)
        self.analysis_state.setText('已暂停')
        if was_pending:
            self.set_answer('\n\n本次分析已停止，以上为未完成的回答。', append=True)
            self.metrics.setText('已停止生成')
        self.set_status('分析已暂停，实时画面继续刷新。')

    def _submit(self, item):
        seq, received, timestamp, frame = item
        prompt = self.prompt.toPlainText().strip()
        if not prompt or len(prompt) > 400:
            self.stop_analysis()
            self.set_status('请填写 1–400 个字符的问题后重新开始。')
            return
        args = copy.copy(self.args)
        args.prompt = prompt
        if self.worker is None:
            context = multiprocessing.get_context('spawn')
            self.connection, child = context.Pipe()
            self.worker = context.Process(target=inference_worker, args=(child,), daemon=True)
            self.worker.start()
            child.close()
            return
        if not self.worker_ready:
            return
        jpeg, size = encode_frame(self.cv2, frame, args.max_side, args.jpeg_quality)
        self.connection.send((args, (jpeg, seq, received, timestamp, size)))
        self.pending = True
        self.last_sent, self.frame_received = seq, received
        self.request_start = time.perf_counter()
        self.next_start = self.request_start + args.interval
        self.set_answer('')
        self.answer.setPlaceholderText('正在观察画面，稍等片刻…')
        self.sample.set_frame(self.qimage(frame).scaled(128, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        sample_time = datetime.fromisoformat(timestamp).astimezone().strftime('%H:%M:%S')
        self.sample_info.setText(f'{sample_time} 的画面')
        self.sample.setToolTip(f'本次提交的取样画面 · 帧 #{seq}')
        self.analysis_state.setText('正在分析')
        self.set_status('Cosmos 正在解读取样画面，实时预览继续刷新。')

    def _finish(self, result):
        self.pending = False
        self.last_result = result
        self.completed += 1
        self.history.append(result)
        self.history = self.history[-20:]
        try:
            self.log.write(json.dumps(result, ensure_ascii=False) + '\n')
            self.log.flush()
        except OSError as exc:
            self.stop_analysis()
            self.set_status(f'无法保存结果，已暂停：{exc}')
            return
        self.set_answer(result.get('answer') or '未收到回答。')
        duration = result.get('total_seconds')
        first = result.get('first_text_seconds')
        first_text = f'{first:.1f} 秒' if first is not None else '—'
        duration_text = f'{duration:.1f} 秒' if duration is not None else '—'
        self.metrics.setText(f'首字 {first_text}    ·    总耗时 {duration_text}')
        if result['status'] == 'error':
            self.stop_analysis()
            self.analysis_state.setText('分析失败')
            self.set_answer('\n\n' + result.get('error', '请求失败'), append=True)
            self.set_status('调用失败，已暂停。点击顶部模型状态可查看服务日志。')
            self.service.start(launch=False)
        else:
            if self.once:
                self.stop_analysis()
            self.analysis_state.setText('已完成')
            if result['status'] == 'truncated':
                self.set_answer('\n\n回答未完整结束，请缩短问题。', append=True)
                self.analysis_state.setText('回答未完成')
            self.set_status(f'已完成 {self.completed} 次分析。' +
                            ('下一次将使用最新画面。' if self.running else '随时可以继续，或换一个问题。'))

    def _event(self, kind, data):
        if kind == 'diagnostics':
            self.diagnostics_busy = False
            self.text_window('运行环境检查', data)
            self.set_status('环境检查已完成，请查看检查结果。')
        elif kind == 'camera_closed':
            self.camera_closing = False
            self.camera_button.setEnabled(True)
            self.device_combo.setEnabled(True)
        elif kind == 'model':
            if self.model_state == 'stopping' and data != 'offline':
                return
            self.model_state = data
            if data == 'ready':
                self.model_button.setText('模型已就绪')
                self.model_button.setIcon(icon('dot', '#36a268', 14))
                self.set_status('模型已就绪。' + ('正在准备分析…' if self.running else '可以开始分析。'))
            elif data == 'loading':
                self.model_button.setText('模型加载中')
                self.model_button.setIcon(icon('dot', '#d69a32', 14))
                self.set_status('模型正在加载，首次启动需要几分钟。')
            else:
                self.model_button.setText('模型待启动')
                self.model_button.setIcon(icon('dot', '#a1a6b0', 14))
                if not self.running:
                    self.set_status('首次使用：请在顶部“…” → 设置中选择模型目录。'
                                    if model_files_error(self.settings['model_dir']) else
                                    '点击开始分析，会自动启动模型。')
                else:
                    QTimer.singleShot(100, lambda: self.service.start() if self.running and not self.closing else None)
        elif kind == 'model_error':
            self.stop_analysis()
            self.model_state = 'error'
            self.model_button.setText('模型需要检查')
            self.model_button.setIcon(icon('dot', '#d66a5a', 14))
            self.set_status(data)

    def tick(self):
        if self.closing:
            return
        try:
            for _ in range(40):
                try:
                    self._event(*self.events.get_nowait())
                except queue.Empty:
                    break
            if self.connection is not None:
                for _ in range(200):
                    if not self.connection.poll():
                        break
                    kind, data = self.connection.recv()
                    if kind == 'ready':
                        self.worker_ready = True
                    elif kind == 'text':
                        self.set_answer(data, append=True)
                        self.analysis_state.setText('正在回答')
                    elif kind == 'result':
                        self._finish(data)
                        break
            if self.worker is not None and not self.worker.is_alive():
                raise RuntimeError('推理客户端已退出，请重新开始。')
            if self.service.process is not None and self.model_state == 'ready' and self.service.process.poll() is not None:
                self._event('model_error', '模型服务已退出，请查看服务日志。')
            if self.camera is not None:
                with self.camera.condition:
                    error, item = self.camera.error, self.camera.latest
                if error:
                    self.stop_camera()
                    self.set_status(error)
                elif item is None:
                    if time.perf_counter() - self.camera_opened > self.args.frame_timeout:
                        self.stop_camera()
                        self.set_status('等待相机超时，请检查连接和设备选择。')
                else:
                    seq, received, timestamp, frame = item
                    if time.perf_counter() - received > self.args.max_frame_age:
                        self.stop_camera()
                        self.set_status('相机画面已停止更新，请检查 USB 连接后重新打开。')
                    else:
                        self.draw_frame(seq, frame)
                        if self.running and not self.pending and self.model_state == 'ready' and seq > self.last_sent and time.perf_counter() >= self.next_start:
                            self._submit(item)
            if self.pending:
                self.metrics.setText(f'正在生成回答    ·    {time.perf_counter() - self.request_start:.1f} 秒')
            if self.frame_received is not None:
                age = time.perf_counter() - self.frame_received
                self.sample_age.setText(f'取样于 {age:.0f} 秒前 · 非实时画面')
        except (OSError, EOFError, ValueError, RuntimeError, self.cv2.error) as exc:
            self.stop_analysis()
            self.set_status(f'操作失败：{exc}')

    def qimage(self, frame):
        rgb = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB)
        return QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()

    def draw_frame(self, seq, frame):
        if seq == self.shown:
            return
        if self.shown == 0 and not self.running and self.model_state != 'loading':
            self.set_status('相机已连接，点击“开始连续分析”即可。')
        self.shown = seq
        image = self.qimage(frame)
        self.video.set_frame(image)
        if self.preview_window is not None and self.preview_window.isVisible():
            self.preview_window.video.set_frame(image)
        self.camera_label.setText('●  实时预览')
        self.camera_label.setStyleSheet('color: #50826b; font-size: 11px;')
        self.resolution.setText(f'{frame.shape[1]} × {frame.shape[0]}')
        self.preview_updates += 1

    def expand_preview(self):
        if self.preview_window is None:
            dialog = QDialog(self)
            dialog.setWindowTitle('Cosmos · 实时画面')
            dialog.resize(1000, 760)
            layout = box(dialog, margins=14)
            dialog.video = VideoView()
            layout.addWidget(dialog.video)
            self.preview_window = dialog
        self.preview_window.video.set_frame(self.video.image)
        self.preview_window.show()
        self.preview_window.raise_()

    def text_window(self, title, text):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(760, 560)
        layout = box(dialog, margins=24, spacing=16)
        layout.addWidget(label(title, 'sectionTitle'))
        content = QPlainTextEdit()
        content.setReadOnly(True)
        content.setPlainText(text)
        layout.addWidget(content, 1)
        layout.addWidget(button('完成', dialog.close, 'primary'), 0, Qt.AlignRight)
        self.dialogs.append(dialog)
        dialog.finished.connect(lambda: self.dialogs.remove(dialog))
        dialog.show()

    def show_history(self):
        rows = []
        for result in reversed(self.history):
            rows.append(f"{result.get('frame_received_at', '')}  /  {result['status']}\n"
                        f"问题：{result.get('prompt', '')}\n{result.get('answer', '')}\n"
                        f"{result.get('error', '')}")
        self.text_window('最近的分析 · 最多 20 条', '\n\n──────────\n\n'.join(rows) or '还没有分析记录。完成一次分析后，可以在这里回看。')

    def show_server_log(self):
        try:
            with self.service.log_path.open('rb') as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 24000))
                text = stream.read().decode('utf-8', errors='replace')
        except FileNotFoundError:
            text = '本窗口尚未启动模型，因此没有服务日志。'
        self.text_window('模型服务日志', text)

    def show_guide(self):
        text = (ROOT/'docs/camera-ui-guide.md').read_text(encoding='utf-8')
        self.text_window('相机助手 · 使用说明', text)

    def closeEvent(self, event):
        if self.cleaned:
            event.accept()
            return
        event.ignore()
        if self.closing:
            return
        self.closing = True
        self.timer.stop()
        self.stop_analysis()
        self.set_status('正在关闭相机和本窗口启动的模型，请稍候…')
        self.model_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.once_button.setEnabled(False)
        self.camera_button.setEnabled(False)
        camera, self.camera = self.camera, None
        finished = threading.Event()
        failure = []
        def cleanup():
            try:
                if camera is not None:
                    camera.close()
                self.service.stop()
            except Exception as exc:
                failure.append(str(exc))
            finally:
                finished.set()
        threading.Thread(target=cleanup, daemon=True).start()
        def wait_close():
            if not finished.is_set():
                QTimer.singleShot(100, wait_close)
            elif failure:
                self.closing = False
                for b in (self.model_button, self.run_button, self.once_button, self.camera_button):
                    b.setEnabled(True)
                self.device_combo.setEnabled(True)
                self.video.clear()
                self.set_status('清理未完成：' + failure[0] + '。可再次关闭窗口重试。')
                self.timer.start(80)
            else:
                self.log.close()
                self.cleaned = True
                self.close()
        wait_close()

"""First-run dialog; long-running work stays in a cancellable subprocess."""
import json
from pathlib import Path
import sys

from PyQt5.QtCore import QProcess, QTimer
from PyQt5.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
                            QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
                            QPushButton, QVBoxLayout)
from app_runtime import ROOT, load_settings, model_files_error
from app_setup import MODEL_PAGE, managed_model_dir


class SetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('准备 Cosmos 世界模型 · Cosmos 相机助手')
        self.resize(760, 640)
        self.setMinimumSize(640, 560)
        self.process = None
        self.task = None
        self.finished_setup = False
        self.cancel_requested = False
        self.buffer = ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        heading = QLabel('准备你的 Cosmos 世界模型')
        heading.setStyleSheet('font-size: 24px; font-weight: 600;')
        layout.addWidget(heading)
        intro = QLabel('完成一次准备，以后打开应用即可分析相机画面。\n'
                       '适用于 Jetson Orin · Ubuntu 22.04 · L4T R36.4.x。\n'
                       '首次下载约 2.8 GB 模型和大型推理镜像，建议预留 25 GiB 磁盘空间。')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.mode = QComboBox()
        self.mode.addItems(['自动下载已验证版本', '使用已有的完整模型目录'])
        layout.addWidget(self.mode)
        row = QHBoxLayout()
        self.model = QLineEdit(str(managed_model_dir()))
        self.model.setAccessibleName('模型目录')
        row.addWidget(self.model, 1)
        self.browse = QPushButton('选择文件夹')
        self.browse.clicked.connect(self.choose_model)
        row.addWidget(self.browse)
        layout.addLayout(row)
        self.license_note = QLabel('先在 <a href="' + MODEL_PAGE + '">模型作者页面</a> 阅读条款并申请访问，'
                                  '获批后填写 <a href="https://huggingface.co/settings/tokens">Hugging Face 读取 Token</a>。'
                                  'Token 仅用于本次下载，不保存到配置或日志。')
        self.license_note.setOpenExternalLinks(True)
        self.license_note.setWordWrap(True)
        layout.addWidget(self.license_note)
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText('hf_…（已有模型无需填写）')
        self.token.setAccessibleName('Hugging Face 读取 Token')
        layout.addWidget(self.token)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setPlainText('点击“开始准备”检查环境、下载并验证文件。断网或退出后，可再次打开继续。\n'
                             '已有 JetPack、Docker 权限和本地模型时，会直接复用。')
        layout.addWidget(self.log, 1)
        buttons = QHBoxLayout()
        self.host = QPushButton('配置系统环境…')
        self.host.clicked.connect(self.configure_host)
        self.host.setEnabled(Path('/usr/bin/cosmos-camera-setup-host').exists())
        buttons.addWidget(self.host)
        buttons.addStretch()
        self.cancel = QPushButton('稍后再说')
        self.cancel.clicked.connect(self.reject)
        buttons.addWidget(self.cancel)
        self.start = QPushButton('开始准备')
        self.start.setObjectName('primary')
        self.start.clicked.connect(self.start_setup)
        buttons.addWidget(self.start)
        layout.addLayout(buttons)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        try:
            settings = load_settings()
            if not model_files_error(settings['model_dir']):
                self.mode.setCurrentIndex(1)
                self.model.setText(settings['model_dir'])
        except ValueError as exc:
            self.log.appendPlainText(str(exc))
        self.mode_changed()

    def mode_changed(self):
        existing = self.mode.currentIndex() == 1
        self.model.setReadOnly(not existing)
        self.browse.setEnabled(existing)
        self.token.setEnabled(not existing)
        if not existing:
            self.model.setText(str(managed_model_dir()))

    def choose_model(self):
        path = QFileDialog.getExistingDirectory(self, '选择完整的 Cosmos 模型目录', self.model.text())
        if path:
            self.model.setText(path)

    def set_running(self, running):
        for widget in (self.start, self.mode, self.model, self.token, self.browse, self.host):
            widget.setEnabled(not running)
        if not running:
            self.host.setEnabled(Path('/usr/bin/cosmos-camera-setup-host').exists())
            self.mode_changed()
        self.cancel.setEnabled(self.task != 'host' or not running)
        self.cancel.setText('暂停准备' if running else '稍后再说')
        self.progress.setRange(0, 0 if running else 100)

    def launch(self, program, arguments, task, payload=None):
        self.task = task
        self.cancel_requested = False
        self.buffer = ''
        self.set_running(True)
        process = QProcess(self)
        self.process = process
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(self.read_output)
        process.finished.connect(self.completed)
        process.errorOccurred.connect(self.process_error)
        if payload is not None:
            def send():
                process.write((json.dumps(payload) + '\n').encode())
                process.closeWriteChannel()
                payload.clear()
            process.started.connect(send)
        process.start(program, arguments)

    def start_setup(self):
        if self.finished_setup:
            self.accept()
            return
        options = dict(token=self.token.text().strip(),
                       model_dir=self.model.text().strip() if self.mode.currentIndex() == 1 else None)
        self.token.clear()
        self.log.appendPlainText('开始准备…')
        self.launch(sys.executable, ['-s', '-B', '-u', str(ROOT/'scripts/app_setup.py'), '--json-input'],
                    'download', options)

    def configure_host(self):
        answer = QMessageBox.question(self, '配置系统环境',
            '此操作会请求管理员授权：启用 Docker、在需要时注册 NVIDIA runtime 并重启 Docker，'
            '以及将当前用户加入 docker 和 video 组。\n\n'
            'Docker 组具有管理员级能力；重启 Docker 可能影响正在运行的容器。'
            '如果本次新增了用户组权限，需要注销桌面并重新登录。\n\n是否继续？')
        if answer == QMessageBox.Yes:
            self.launch('/usr/bin/pkexec', ['/usr/bin/cosmos-camera-setup-host'], 'host')

    def read_output(self):
        if self.process is None:
            return
        self.buffer += bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace').replace('\r', '\n')
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            if line.strip():
                self.log.appendPlainText(line)

    def process_error(self, error):
        if error == QProcess.FailedToStart:
            self.log.appendPlainText('无法启动准备进程，请重新安装应用或检查系统图形授权组件。')
            self.completed(1, QProcess.CrashExit)

    def completed(self, code, status):
        if self.process is None:
            return
        self.read_output()
        if self.buffer.strip():
            self.log.appendPlainText(self.buffer)
        self.process.deleteLater()
        self.process = None
        self.set_running(False)
        if code == 0 and status == QProcess.NormalExit and self.task == 'download' and not self.cancel_requested:
            self.finished_setup = True
            self.progress.setValue(100)
            self.start.setText('进入相机助手')
            for widget in (self.mode, self.model, self.browse, self.token, self.host):
                widget.setEnabled(False)
        else:
            self.progress.setValue(0)
            self.start.setText('重试准备' if self.task == 'download' else '开始准备')
            if self.cancel_requested:
                self.log.appendPlainText('已暂停；已下载的文件会保留，可以继续准备。')
            elif code != 0:
                self.log.appendPlainText('尚未准备完成，请按上方提示处理后重试。')

    def reject(self):
        if self.process is not None:
            if self.task == 'host':
                return  # Do not interrupt privileged configuration midway.
            self.cancel_requested = True
            self.cancel.setEnabled(False)
            self.process.terminate()
            process = self.process
            def force_stop():
                if self.process is process and process.state() != QProcess.NotRunning:
                    process.kill()
            QTimer.singleShot(8000, force_stop)
            return
        super().reject()

    def closeEvent(self, event):
        if self.process is not None:
            event.ignore()
            self.reject()
        else:
            event.accept()

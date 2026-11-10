"""Open the native Qt camera and Cosmos analysis workspace."""
import argparse
import multiprocessing
import os
from camera_infer import ROOT
from camera_ui_backend import ModelService, inference_worker
from app_runtime import VERSION, logs_dir, load_settings, default_settings, installed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=None)
    parser.add_argument('--version', action='version', version='Cosmos Camera ' + VERSION)
    parser.add_argument('--check', action='store_true', help='检查运行环境，不打开窗口或加载模型')
    parser.add_argument('--no-camera', action='store_true', help='打开窗口时不自动开启相机')
    parser.add_argument('--setup', action='store_true', help='打开首次运行准备向导')
    args = parser.parse_args()
    if args.check:
        from app_diagnostics import check_environment, format_report
        try:
            report = check_environment(load_settings()['model_dir'])
            print(format_report(report))
            return 0 if report['ready'] else 1
        except ValueError as exc:
            print(str(exc))
            return 1
    from camera_preview import prepare_display
    prepare_display()
    # The pip OpenCV wheel advertises its own Qt plugins. Use the system Qt
    # runtime for the application, even when OpenCV is imported later.
    import cv2
    for key in ('QT_QPA_PLATFORM_PLUGIN_PATH', 'QT_QPA_FONTDIR'):
        if 'cv2' in os.environ.get(key, ''):
            os.environ.pop(key, None)
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication, QMessageBox
    from camera_ui_qt import CameraApp, configure_application
    import fcntl
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(['cosmos-camera'])
    configure_application(app)
    logs_dir().mkdir(parents=True, exist_ok=True)
    with (logs_dir()/'camera-ui.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            QMessageBox.information(None, 'Cosmos 相机助手', '界面已经打开，请切换到现有的“Cosmos 相机助手”窗口。')
            return
        try:
            settings = load_settings()
        except ValueError as exc:
            QMessageBox.warning(None, '配置需要检查', str(exc) + '\n原文件已保留。本次使用默认设置。')
            settings = default_settings()
        if args.source is not None:
            settings['camera_source'] = args.source
        from app_setup import needs_setup
        if args.setup or (installed() and needs_setup(settings)):
            from setup_ui import SetupDialog
            dialog = SetupDialog()
            if dialog.exec_() != dialog.Accepted:
                return 0
            settings = load_settings()
            if args.source is not None:
                settings['camera_source'] = args.source
        window = CameraApp(auto_camera=not args.no_camera, settings=settings)
        window.show()
        app.exec_()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())

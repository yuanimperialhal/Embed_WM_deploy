"""Manual Qt integration check with a real USB camera and a local SSE fixture.

Run on the Jetson desktop: python3 tests/qt_camera_smoke.py
No model weights are loaded. Fixture replies are explicitly marked as UI tests.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time
import tempfile

ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = Path(os.environ.get('COSMOS_TEST_APP_ROOT', str(ROOT)))
sys.path.insert(0, str(APP_ROOT/'scripts'))


class Fixture(BaseHTTPRequestHandler):
    requests = []
    fail_next = False

    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append(payload)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        fail = Fixture.fail_next
        Fixture.fail_next = False
        try:
            for word in ('[界面测试] ', '已收到取样图像。', '流式回答显示正常。'):
                self.wfile.write(('data: ' + json.dumps({'choices': [{'delta': {'content': word}}]}) + '\n\n').encode())
                self.wfile.flush()
                time.sleep(.4)
            if not fail:
                self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    config_temp = tempfile.TemporaryDirectory(prefix='cosmos-qt-settings-')
    os.environ['XDG_CONFIG_HOME'] = config_temp.name
    from camera_preview import prepare_display
    prepare_display()
    import cv2
    for key in ('QT_QPA_PLATFORM_PLUGIN_PATH', 'QT_QPA_FONTDIR'):
        if 'cv2' in os.environ.get(key, ''):
            os.environ.pop(key, None)
    from PyQt5.QtCore import QPoint, QRect, QTimer
    from PyQt5.QtWidgets import QApplication, QPushButton
    from camera_ui_qt import CameraApp, configure_application
    from app_runtime import default_settings, load_settings
    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    app = QApplication(['cosmos-camera-test'])
    configure_application(app)
    settings = default_settings()
    if os.environ.get('COSMOS_TEST_MODEL_DIR'):
        settings['model_dir'] = os.environ['COSMOS_TEST_MODEL_DIR']
    window = CameraApp(settings=settings)
    window.args.url = f'http://127.0.0.1:{server.server_port}/v1/chat/completions'
    window.show()
    started = time.monotonic()
    phase = 0
    partial = False
    preview_before = 0
    result = {}

    def inside(widget):
        assert widget.isVisible(), widget.objectName()
        rect = QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())
        assert window.rect().contains(rect), (widget.objectName(), rect.getRect())

    def check_layout():
        for widget in (window.run_button, window.once_button, window.status, window.model_button,
                       window.prompt, window.camera_button, window.result_stack):
            inside(widget)
        assert window.run_button.width() > 140
        assert window.answer.height() >= 70

    def advance():
        nonlocal phase, partial, preview_before
        try:
            if time.monotonic() - started > 60:
                raise RuntimeError(f'Timed out: phase {phase}; {window.status.text()}')
            if phase == 0 and window.shown > 5 and window.model_state == 'offline':
                check_layout()
                window.grab().save(str(window.run_dir/'qt-desktop-preview.png'))
                window.resize(1000, 720)
                phase = 1
            elif phase == 1:
                check_layout()
                assert window.width() == 1000 and window.height() == 720
                window.grab().save(str(window.run_dir/'qt-minimum-preview.png'))
                window.resize(1220, 820)
                window.set_prompt('保存配置测试')
                def save_dialog():
                    dialog = app.activeModalWidget()
                    save_button = next(b for b in dialog.findChildren(QPushButton) if b.text() == '保存设置')
                    save_button.click()
                QTimer.singleShot(100, save_dialog)
                # The timeout also dismisses the dialog if validation fails.
                QTimer.singleShot(2000, lambda: app.activeModalWidget().reject() if app.activeModalWidget() else None)
                window.show_settings()
                assert load_settings()['prompt'] == '保存配置测试'
                window.show_about()
                window.dialogs[-1].close()
                window._event('model', 'ready')
                window.set_prompt('界面测试第一问')
                window.once_button.click()
                preview_before = window.preview_updates
                phase = 2
            elif phase == 2:
                if window.pending and window.answer.toPlainText().strip():
                    partial = True
                if window.completed == 1:
                    assert partial, 'No partial answer observed'
                    assert window.last_result['answer'] == '[界面测试] 已收到取样图像。流式回答显示正常。'
                    assert window.preview_updates - preview_before > 3
                    assert window.worker is None and not window.pending and not window.running
                    assert Fixture.requests[0]['messages'][0]['content'][1]['text'] == '界面测试第一问'
                    window.grab().save(str(window.run_dir/'qt-fixture-result.png'))
                    window.show_history()
                    assert window.dialogs[-1].isVisible()
                    window.dialogs[-1].close()
                    window.show_guide()
                    window.dialogs[-1].close()
                    window.show_server_log()
                    window.dialogs[-1].close()
                    window.expand_preview()
                    assert window.preview_window.video.image is not None
                    window.preview_window.close()
                    window.stop_camera()
                    phase = 3
            elif phase == 3 and not window.camera_closing:
                assert window.camera is None and window.device_combo.isEnabled()
                window.camera_button.click()
                phase = 4
            elif phase == 4 and window.shown > 5:
                window.set_prompt('')
                window.once_button.click()
                assert not window.running
                window.set_prompt('界面测试第二问')
                window.run_button.click()
                phase = 5
            elif phase == 5 and len(Fixture.requests) >= 2:
                window.run_button.click()
                assert not window.running and not window.pending and window.worker is None
                assert Fixture.requests[1]['messages'][0]['content'][1]['text'] == '界面测试第二问'
                preview_before = window.preview_updates
                phase = 6
            elif phase == 6 and window.preview_updates > preview_before + 5:
                Fixture.fail_next = True
                window.once_button.click()
                phase = 7
            elif phase == 7 and window.completed == 2:
                assert window.last_result['status'] == 'error'
                assert not window.running and not window.pending
                assert 'incomplete' in window.answer.toPlainText()
                result.update(status='passed', real_camera=True, model='local SSE fixture (no model loaded)',
                              layout_default=True, layout_minimum=True, partial_stream=True,
                              preview_during_inference=True, camera_reopen=True, prompt_update=True,
                              cancel=True, incomplete_stream_error=True, dialogs=True, settings_persistence=True,
                              code_root=str(APP_ROOT),
                              preview_updates=window.preview_updates)
                timer.stop()
                window.close()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            result.update(status='failed', error=repr(exc), phase=phase)
            timer.stop()
            window.close()

    timer = QTimer()
    timer.timeout.connect(advance)
    timer.start(100)
    print('Qt integration check: ' + str(window.run_dir), flush=True)
    app.exec_()
    server.shutdown()
    result['cleanup'] = window.cleaned
    (window.run_dir/'qt-smoke-report.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
    config_temp.cleanup()
    return result.get('status') != 'passed'


if __name__ == '__main__':
    sys.exit(main())

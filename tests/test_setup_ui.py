"""Exercise first-run process lifecycle in Qt without camera, network or Docker."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from PyQt5.QtWidgets import QApplication, QDialog
import setup_ui


class FirstRunUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(['setup-ui-test'])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root/'scripts').mkdir()
        for target, kwargs in [('ROOT', {'new': self.root}),
                               ('load_settings', {'side_effect': ValueError('isolated test')})]:
            mock = patch.object(setup_ui, target, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)
        self.dialog = setup_ui.SetupDialog()
        self.addCleanup(self.dialog.deleteLater)

    def run_until(self, condition, timeout=5):
        end = time.monotonic() + timeout
        while not condition() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(condition(), self.dialog.log.toPlainText())

    def script(self, content):
        (self.root/'scripts/app_setup.py').write_text(content)

    def test_success_clears_token_and_allows_entering_application(self):
        self.script('import json,sys\ndata=json.loads(sys.stdin.readline())\nassert data["token"] == "hf_example"\nprint("准备完成", flush=True)\n')
        self.dialog.token.setText('hf_example')
        self.dialog.start_setup()
        self.assertEqual(self.dialog.token.text(), '')
        self.run_until(lambda: self.dialog.process is None)
        self.assertTrue(self.dialog.finished_setup)
        self.assertEqual(self.dialog.start.text(), '进入相机助手')
        self.assertNotIn('hf_example', self.dialog.log.toPlainText())
        self.dialog.start_setup()
        self.assertEqual(self.dialog.result(), QDialog.Accepted)

    def test_failure_stays_retryable_and_does_not_report_ready(self):
        self.script('import sys\nsys.stdin.readline()\nprint("模型访问被拒绝", flush=True)\nsys.exit(1)\n')
        self.dialog.start_setup()
        self.run_until(lambda: self.dialog.process is None)
        self.assertFalse(self.dialog.finished_setup)
        self.assertTrue(self.dialog.start.isEnabled())
        self.assertIn('模型访问被拒绝', self.dialog.log.toPlainText())

    def test_cancel_reaps_running_process_and_allows_retry(self):
        self.script('import sys,time\nsys.stdin.readline()\nprint("下载中", flush=True)\ntime.sleep(30)\n')
        self.dialog.start_setup()
        self.run_until(lambda: '下载中' in self.dialog.log.toPlainText())
        self.dialog.reject()
        self.run_until(lambda: self.dialog.process is None)
        self.assertFalse(self.dialog.finished_setup)
        self.assertTrue(self.dialog.start.isEnabled())
        self.assertIn('已暂停', self.dialog.log.toPlainText())


if __name__ == '__main__':
    unittest.main()

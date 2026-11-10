"""GUI backend checks; no camera, desktop or model weights required."""
import io
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from bench_image import read_stream
from camera_ui_backend import ModelService


def stream(*events, done=True):
    raw = b''.join(b'data: ' + json.dumps(event).encode() + b'\n\n' for event in events)
    return io.BytesIO(raw + (b'data: [DONE]\n\n' if done else b''))


class StreamTests(unittest.TestCase):
    def test_callback_receives_only_answer_and_preserves_validation(self):
        result = dict(answer='', reasoning='', first_text_seconds=None,
                      first_reasoning_seconds=None, received_done=False, finish_reason=None)
        chunks = []
        response = stream(
            {'choices': [{'delta': {'role': 'assistant'}}]},
            {'choices': [{'delta': {'reasoning_content': 'reason'}}]},
            {'choices': [{'delta': {'content': '相机'}}]},
            {'choices': [{'delta': {'content': '画面'}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {'completion_tokens': 4}})
        read_stream(response, result, time.perf_counter(), on_text=chunks.append, echo=False)
        self.assertEqual(chunks, ['相机', '画面'])
        self.assertEqual(result['answer'], '相机画面')
        self.assertEqual(result['reasoning'], 'reason')
        self.assertEqual(result['usage']['completion_tokens'], 4)
        self.assertTrue(result['received_done'])

    def test_disconnect_never_becomes_completed_answer(self):
        result = dict(answer='', reasoning='', first_text_seconds=None,
                      first_reasoning_seconds=None, received_done=False, finish_reason=None)
        response = stream({'choices': [{'delta': {'content': 'partial'}}]}, done=False)
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            read_stream(response, result, time.perf_counter(), echo=False)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.events = queue.Queue()
        self.service = ModelService(self.events, Path(self.temp.name)/'server.log')

    def wait_idle(self):
        end = time.monotonic() + 3
        while self.service.busy and time.monotonic() < end:
            time.sleep(0.01)
        self.assertFalse(self.service.busy)

    def test_reuses_existing_service_without_spawning_or_stopping_it(self):
        with patch.object(self.service, 'health', return_value=True), patch('camera_ui_backend.subprocess.Popen') as spawn:
            self.service.start()
            self.wait_idle()
            self.assertEqual(self.events.get_nowait(), ('model', 'ready'))
            self.service.stop()
            spawn.assert_not_called()
            self.assertIsNone(self.service.process)

    def test_probe_does_not_launch_model(self):
        with patch.object(self.service, 'health', side_effect=OSError('offline')), patch('camera_ui_backend.subprocess.Popen') as spawn:
            self.service.start(launch=False)
            self.wait_idle()
            self.assertEqual(self.events.get_nowait(), ('model', 'offline'))
            spawn.assert_not_called()

    def test_failed_start_reports_error(self):
        process = Mock()
        process.poll.return_value = 1
        with patch.object(self.service, 'health', return_value=False), patch('camera_ui_backend.subprocess.Popen', return_value=process):
            self.service.start()
            self.wait_idle()
            self.assertEqual(self.events.get_nowait(), ('model', 'loading'))
            self.assertEqual(self.events.get_nowait()[0], 'model_error')
            self.service.stop()
            process.terminate.assert_not_called()

    def test_stops_only_owned_process(self):
        process = Mock()
        process.poll.return_value = None
        self.service.process = process
        self.service.stop()
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=45)
        self.assertIsNone(self.service.process)

    def test_close_during_health_check_does_not_launch_afterwards(self):
        entered, release = threading.Event(), threading.Event()
        def health():
            entered.set()
            release.wait(2)
            return False
        with patch.object(self.service, 'health', side_effect=health), patch('camera_ui_backend.subprocess.Popen') as spawn:
            self.service.start()
            self.assertTrue(entered.wait(1))
            self.service.stop()
            release.set()
            self.wait_idle()
            spawn.assert_not_called()
            self.assertTrue(self.events.empty())


if __name__ == '__main__':
    unittest.main()

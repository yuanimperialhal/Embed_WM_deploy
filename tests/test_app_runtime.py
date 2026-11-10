"""Release-critical settings and model-path behavior, without Qt or hardware."""
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import app_runtime as runtime
from camera_ui_backend import ModelService
from run_single_image import server_command


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        deployment = patch.object(runtime, 'DEPLOYMENT_PATH', self.root/'deployment.json')
        deployment.start()
        self.addCleanup(deployment.stop)
        env = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root/'config'),
                                      'XDG_STATE_HOME': str(self.root/'state'),
                                      'XDG_CACHE_HOME': str(self.root/'cache'),
                                      'XDG_DATA_HOME': str(self.root/'data')})
        env.start()
        self.addCleanup(env.stop)

    def test_installed_paths_do_not_require_writable_program_directory(self):
        with patch.object(runtime, 'ROOT', self.root/'read-only-code'), patch.object(runtime, 'installed', return_value=True):
            self.assertEqual(runtime.logs_dir(), self.root/'state/cosmos-camera/logs')
            self.assertEqual(runtime.cache_dir(), self.root/'cache/cosmos-camera')
            self.assertEqual(runtime.default_settings()['model_dir'], str(self.root/'data/cosmos-camera/models/cosmos-download'))
            settings = runtime.default_settings()
            runtime.save_settings(settings)
            self.assertEqual(runtime.load_settings(), settings)
            self.assertFalse((self.root/'read-only-code').exists())

    def test_saved_settings_survive_restart_and_interrupted_write(self):
        first = dict(runtime.default_settings(), camera_source='/dev/video2', prompt='桌面上有什么？')
        runtime.save_settings(first)
        self.assertEqual(runtime.load_settings(), first)
        raw = runtime.settings_path().read_bytes()
        with patch('app_runtime.os.fsync', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                runtime.save_settings(dict(first, prompt='new question'))
        self.assertEqual(runtime.settings_path().read_bytes(), raw)
        self.assertEqual(list(runtime.settings_path().parent.glob('.settings-*')), [])

    def test_corrupt_and_future_config_is_not_silently_overwritten(self):
        path = runtime.settings_path()
        path.parent.mkdir(parents=True)
        for content in ('{broken', json.dumps({'schema_version': 2})):
            path.write_text(content)
            with self.assertRaises(ValueError):
                runtime.load_settings()
            self.assertEqual(path.read_text(), content)

    def test_relative_xdg_override_ignored(self):
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': 'relative-folder'}):
            self.assertEqual(runtime.settings_path(), Path.home()/'.config/cosmos-camera/settings.json')

    def test_invalid_model_mount_or_prompt_rejected_without_replacing_config(self):
        initial = runtime.default_settings()
        runtime.save_settings(initial)
        for changes in ({'model_dir': 'relative/model'}, {'model_dir': '/tmp/model:bad'},
                        {'prompt': ''}, {'prompt': 'a'*401}):
            with self.assertRaises(ValueError):
                runtime.save_settings(dict(initial, **changes))
            self.assertEqual(runtime.load_settings(), initial)

    def test_model_paths_with_spaces_are_single_subprocess_arguments(self):
        command = server_command('test-container', model_dir='/tmp/my model', cache_dir='/tmp/my cache')
        self.assertIn('/tmp/my model:/model:ro', command)
        self.assertIn('/tmp/my cache:/cache', command)
        self.assertNotIn(str(runtime.ROOT/'models/cosmos-download') + ':/model:ro', command)

    def test_missing_model_reports_actionable_error_without_spawning(self):
        events = queue.Queue()
        service = ModelService(events, self.root/'server.log', self.root/'missing-model')
        service.health = Mock(return_value=False)
        with patch('camera_ui_backend.subprocess.Popen') as spawn:
            service._start(True)
            kind, message = events.get_nowait()
            self.assertEqual(kind, 'model_error')
            self.assertIn('设置', message)
            spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()

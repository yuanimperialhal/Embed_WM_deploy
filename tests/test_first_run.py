"""Release-critical download, credential, rollback and provisioning behavior."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
sys.path.insert(0, str(ROOT/'packaging'))
import app_setup as setup
import setup_host
from configure_repository import configure, repository_name


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root/'weights.bin'
        self.content = b'verified model data'
        self.record = dict(name=self.path.name, bytes=len(self.content),
                           sha256=hashlib.sha256(self.content).hexdigest())
        self.opener = Mock()
        self.url = 'https://huggingface.co/repo/resolve/commit/weights.bin'

    def download(self):
        setup.download_file(self.url, self.path, self.record, 'hf_test', self.opener, lambda _: None)

    def test_download_is_verified_and_existing_file_needs_no_network(self):
        self.opener.open.return_value = Response(self.content)
        self.download()
        self.assertEqual(self.path.read_bytes(), self.content)
        self.download()
        self.opener.open.assert_called_once()
        self.assertFalse(self.path.with_suffix('.bin.partial').exists())

    def test_token_is_not_forwarded_to_cdn_redirect(self):
        self.opener.open.return_value = Response(self.content)
        self.download()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer hf_test')
        redirected = setup.HTTPSRedirect().redirect_request(request, None, 302, '', {}, 'https://cdn.example/weights')
        self.assertIsNone(redirected.get_header('Authorization'))
        same_origin = setup.HTTPSRedirect().redirect_request(request, None, 302, '', {}, 'https://huggingface.co/api/resolve-cache/file')
        self.assertEqual(same_origin.get_header('Authorization'), 'Bearer hf_test')
        with self.assertRaises(setup.SetupError):
            setup.HTTPSRedirect().redirect_request(request, None, 302, '', {}, 'http://cdn.example/weights')

    def test_partial_resumes_with_correct_range(self):
        self.path.with_suffix('.bin.partial').write_bytes(self.content[:5])
        self.opener.open.return_value = Response(self.content[5:], 206,
                                                {'Content-Range': f'bytes 5-{len(self.content)-1}/{len(self.content)}'})
        self.download()
        self.assertEqual(self.opener.open.call_args.args[0].get_header('Range'), 'bytes=5-')
        self.assertEqual(self.path.read_bytes(), self.content)

    def test_origin_ignoring_range_restarts_instead_of_appending(self):
        self.path.with_suffix('.bin.partial').write_bytes(b'bad partial')
        self.opener.open.return_value = Response(self.content)
        self.download()
        self.assertEqual(self.path.read_bytes(), self.content)

    def test_bad_range_is_rejected_without_changing_partial(self):
        partial = self.path.with_suffix('.bin.partial')
        partial.write_bytes(self.content[:5])
        self.opener.open.return_value = Response(self.content[5:], 206, {'Content-Range': 'bytes 0-5/6'})
        with self.assertRaises(setup.SetupError):
            self.download()
        self.assertEqual(partial.read_bytes(), self.content[:5])
        self.assertFalse(self.path.exists())

    def test_corrupt_download_cannot_become_model_file(self):
        self.opener.open.return_value = Response(b'x'*len(self.content))
        with self.assertRaises(setup.SetupError):
            self.download()
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.with_suffix('.bin.partial').exists())

    def test_complete_partial_is_verified_and_promoted_without_network(self):
        self.path.with_suffix('.bin.partial').write_bytes(self.content)
        self.download()
        self.opener.open.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.content)

    def test_existing_user_file_is_never_overwritten(self):
        self.path.write_bytes(b'own model')
        with self.assertRaises(setup.SetupError):
            self.download()
        self.assertEqual(self.path.read_bytes(), b'own model')
        self.opener.open.assert_not_called()

    def test_symlink_target_is_not_followed(self):
        target = self.root/'user-file'
        target.write_bytes(b'keep')
        self.path.with_suffix('.bin.partial').symlink_to(target)
        with self.assertRaises(setup.SetupError):
            self.download()
        self.assertEqual(target.read_bytes(), b'keep')

    def test_authorization_failure_has_actionable_error_without_secret(self):
        self.opener.open.side_effect = urllib.error.HTTPError(self.url+'?secret=private', 403, 'secret', {}, None)
        with self.assertRaises(setup.SetupError) as result:
            self.download()
        self.assertIn('申请访问', str(result.exception))
        self.assertNotIn('secret', str(result.exception))
        self.opener.open.assert_called_once()

    def test_interrupted_transfer_retries_and_resumes(self):
        self.opener.open.side_effect = [Response(self.content[:5]),
            Response(self.content[5:], 206, {'Content-Range': f'bytes 5-{len(self.content)-1}/{len(self.content)}'})]
        with patch.object(setup.time, 'sleep'):
            self.download()
        self.assertEqual(self.path.read_bytes(), self.content)
        self.assertEqual(self.opener.open.call_count, 2)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.old = dict(schema_version=1, model_dir='/old/model', camera_source='/dev/video8', prompt='保留我的问题')
        for target, value in [('state_dir', self.root/'state'), ('load_settings', self.old),
                              ('check_host', None), ('image_ready', True), ('model_files_error', None)]:
            mock = patch.object(setup, target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        patcher = patch.object(setup.os, 'geteuid', return_value=1000)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_success_keeps_preferences_and_records_ready_after_image_validation(self):
        with patch.object(setup, 'save_settings') as save:
            setup.prepare(self.root/'existing', progress=lambda _: None)
        self.assertEqual(save.call_args.args[0], dict(self.old, model_dir=str(self.root/'existing')))
        self.assertTrue(setup.receipt_path().is_file())

    def test_failed_pull_never_changes_settings_or_marks_complete(self):
        process = Mock()
        process.wait.return_value = 1
        process.poll.return_value = 1
        with patch.object(setup, 'image_ready', return_value=False), \
                patch.object(setup.subprocess, 'Popen', return_value=process), \
                patch.object(setup, 'save_settings') as save:
            with self.assertRaises(setup.SetupError):
                setup.prepare(self.root/'existing', progress=lambda _: None)
        save.assert_not_called()
        self.assertFalse(setup.receipt_path().exists())

    def test_cancelled_pull_stops_child_and_does_not_pass_token(self):
        process = Mock()
        process.wait.side_effect = [KeyboardInterrupt, 0]
        process.poll.return_value = None
        with patch.object(setup, 'image_ready', return_value=False), \
                patch.object(setup.subprocess, 'Popen', return_value=process) as spawn, \
                patch.dict(os.environ, {'HF_TOKEN': 'hf_private'}), \
                patch.object(setup, 'save_settings') as save:
            with self.assertRaises(KeyboardInterrupt):
                setup.prepare(self.root/'existing', progress=lambda _: None)
        self.assertNotIn('HF_TOKEN', spawn.call_args.kwargs['env'])
        process.terminate.assert_called_once()
        save.assert_not_called()

    def test_concurrent_preparation_is_rejected(self):
        with setup.setup_lock():
            with self.assertRaises(setup.SetupError):
                with setup.setup_lock():
                    self.fail('second setup acquired lock')

    def test_invalid_token_error_does_not_echo_token(self):
        with self.assertRaises(setup.SetupError) as result:
            setup.prepare(self.root/'existing', token='hf_secret\r\nheader')
        self.assertNotIn('secret', str(result.exception))


class RepositoryTests(unittest.TestCase):
    def test_valid_url_and_bad_repository_inputs(self):
        self.assertEqual(repository_name('https://github.com/someone/cosmos-camera.git'), 'someone/cosmos-camera')
        for value in ('../repo', 'owner/..', 'https://example.org/owner/repo', 'owner/repo/other', 'owner/$(cmd)'):
            with self.assertRaises(ValueError):
                repository_name(value)

    def test_configuring_links_repeatedly_preserves_surrounding_readme(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'config').mkdir()
            (root/'config/release.json').write_text('{"repository": null}')
            (root/'README.md').write_text('before\n<!-- release-links:start -->\nold\n<!-- release-links:end -->\nafter')
            configure('owner/repo', root)
            original = (root/'README.md').read_text()
            configure('owner/repo', root)
            self.assertEqual((root/'README.md').read_text(), original)
            self.assertIn('/releases/latest/download/cosmos-camera_latest_arm64.deb', original)
            self.assertTrue(original.startswith('before\n'))
            self.assertTrue(original.endswith('\nafter'))


class HostConfigurationTests(unittest.TestCase):
    def setUp(self):
        options = [('os.geteuid', {'return_value': 0}),
                   ('pwd.getpwuid', {'return_value': SimpleNamespace(pw_name='desktop-user', pw_uid=1000, pw_gid=1000, pw_shell='/bin/bash')}),
                   ('platform_error', {'return_value': None}),
                   ('shutil.which', {'return_value': '/usr/bin/tool'}),
                   ('os.getgrouplist', {'return_value': [1000, 44, 999]}),
                   ('grp.getgrnam', {'side_effect': lambda name: SimpleNamespace(gr_gid={'video': 44, 'docker': 999}[name])})]
        environment = patch.dict(os.environ, {'SUDO_UID': '1000', 'PKEXEC_UID': ''})
        environment.start()
        self.addCleanup(environment.stop)
        for target, kwargs in options:
            patcher = patch('setup_host.'+target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_existing_runtime_and_groups_need_no_restart_or_usermod(self):
        with patch.object(setup_host.subprocess, 'run', return_value=SimpleNamespace(stdout='{"nvidia": {}}')) as run:
            setup_host.configure_host()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(['systemctl', 'enable', '--now', 'docker'], commands)
        self.assertFalse(any(command[0] in ('usermod', 'nvidia-ctk') or 'restart' in command for command in commands))

    def test_missing_runtime_and_groups_are_configured_without_replacing_groups(self):
        with patch.object(setup_host.subprocess, 'run', return_value=SimpleNamespace(stdout='{"runc": {}}')) as run, \
                patch.object(setup_host.os, 'getgrouplist', return_value=[1000]):
            setup_host.configure_host()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(['nvidia-ctk', 'runtime', 'configure', '--runtime=docker'], commands)
        self.assertIn(['systemctl', 'restart', 'docker'], commands)
        self.assertIn(['usermod', '-aG', 'docker', 'desktop-user'], commands)
        self.assertIn(['usermod', '-aG', 'video', 'desktop-user'], commands)

    def test_unsupported_platform_cannot_mutate_system(self):
        with patch.object(setup_host, 'platform_error', return_value='unsupported'), \
                patch.object(setup_host.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                setup_host.configure_host()
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

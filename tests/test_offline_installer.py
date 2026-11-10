"""Verify the single-file envelope and offline deployment defaults without root."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'packaging'))
sys.path.insert(0, str(ROOT/'scripts'))
from build_offline import create_bundle
from offline_common import validate_image_archive, verify_files
import app_runtime as runtime


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def bundle(self, sources):
        target = self.root/'example.run'
        create_bundle(sources, dict(file_count=len(sources), unpacked_bytes=sum(len(x) for x in sources.values())), target)
        return target

    def execute(self, path, *args):
        return subprocess.run([str(path), *map(str, args)], capture_output=True, text=True, timeout=10)

    def test_run_file_verifies_and_extracts_without_installation(self):
        bundle = self.bundle({'app/readme.txt': '这是离线包'.encode(), 'model/weights': b'1234'})
        self.assertEqual(self.execute(bundle, '--check').returncode, 0)
        destination = self.root/'extracted'
        result = self.execute(bundle, '--extract', destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((destination/'model/weights').read_bytes(), b'1234')
        self.assertEqual((destination/'app/readme.txt').read_text(), '这是离线包')
        self.assertNotEqual(self.execute(bundle, '--extract', destination).returncode, 0)

    def test_corrupt_package_refuses_extraction(self):
        bundle = self.bundle({'model/weights': b'1234'})
        with bundle.open('ab') as stream:
            stream.write(b'corruption')
        result = self.execute(bundle, '--extract', self.root/'bad')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('校验失败', result.stderr)
        self.assertFalse((self.root/'bad').exists())

    def test_path_traversal_is_rejected(self):
        bundle = self.bundle({'../outside': b'bad'})
        result = self.execute(bundle, '--extract', self.root/'extracted')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root/'outside').exists())

    def test_file_manifest_detects_corruption_and_symlinks(self):
        file = self.root/'weights'
        file.write_bytes(b'123')
        record = dict(path='weights', bytes=3, sha256=hashlib.sha256(b'123').hexdigest())
        manifest = {'files': [record]}
        verify_files(self.root, manifest)
        file.write_bytes(b'456')
        with self.assertRaises(ValueError):
            verify_files(self.root, manifest)
        file.unlink()
        (self.root/'actual').write_bytes(b'123')
        file.symlink_to(self.root/'actual')
        with self.assertRaises(ValueError):
            verify_files(self.root, manifest)

    def test_docker_classic_and_containerd_image_identity(self):
        config = json.dumps(dict(architecture='arm64', os='linux')).encode()
        config_id = 'sha256:'+hashlib.sha256(config).hexdigest()
        descriptor = json.dumps({'config': {'digest': config_id}}).encode()
        manifest_id = 'sha256:'+hashlib.sha256(descriptor).hexdigest()
        archive = self.root/'image.tar'
        files = {'manifest.json': json.dumps([{'Config': 'config.json', 'Layers': []}]).encode(),
                 'config.json': config,
                 'index.json': json.dumps({'manifests': [{'digest': manifest_id}]}).encode(),
                 'blobs/sha256/'+manifest_id.split(':')[1]: descriptor}
        with tarfile.open(archive, 'w') as output:
            for name, content in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                output.addfile(info, io.BytesIO(content))
        self.assertEqual(validate_image_archive(archive, config_id), [config_id])
        self.assertEqual(validate_image_archive(archive, manifest_id), [manifest_id, config_id])
        with self.assertRaises(ValueError):
            validate_image_archive(archive, 'sha256:'+'0'*64)

    def test_system_defaults_do_not_override_existing_user_settings(self):
        deployment = self.root/'deployment.json'
        identity = 'sha256:'+'a'*64
        deployment.write_text(json.dumps(dict(schema_version=1, model_dir='/var/lib/cosmos-camera/models/test', image_id=identity)))
        with patch.object(runtime, 'installed', return_value=True), patch.object(runtime, 'DEPLOYMENT_PATH', deployment), \
                patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root/'config')}):
            self.assertEqual(runtime.default_settings()['model_dir'], '/var/lib/cosmos-camera/models/test')
            self.assertEqual(runtime.deployed_image('registry/default'), identity)
            runtime.save_settings(dict(runtime.default_settings(), model_dir='/data/my-chosen-model'))
            self.assertEqual(runtime.load_settings()['model_dir'], '/data/my-chosen-model')
            deployment.write_text('broken json')
            self.assertEqual(runtime.deployed_image('registry/default'), 'registry/default')
        with patch.object(runtime, 'installed', return_value=False), patch.object(runtime, 'DEPLOYMENT_PATH', deployment):
            self.assertEqual(runtime.read_deployment(), {})


if __name__ == '__main__':
    unittest.main()

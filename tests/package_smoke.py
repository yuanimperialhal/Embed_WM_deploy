"""Check a built package in an isolated extraction, without system installation.

python3 tests/package_smoke.py dist/cosmos-camera_0.1.0-1_arm64.deb --gui
--gui needs a free USB camera and a logged-in Jetson desktop.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--gui', action='store_true')
    args = parser.parse_args()
    report_dir = ROOT/'logs'/datetime.now().strftime('package-check-%Y%m%d-%H%M%S')
    report_dir.mkdir(parents=True)
    result = dict(package=str(args.package.resolve()), system_install_tested=False,
                  package_sha256=hashlib.sha256(args.package.read_bytes()).hexdigest())
    with tempfile.TemporaryDirectory(prefix='cosmos-package-check-') as temporary:
        stage = Path(temporary)
        subprocess.run(['dpkg-deb', '--raw-extract', str(args.package.resolve()), str(stage)], check=True)
        app = stage/'usr/lib/cosmos-camera'
        manifest = (stage/'DEBIAN/md5sums').read_text().splitlines()
        for line in manifest:
            digest, relative = line.split('  ', 1)
            path = stage/relative
            assert hashlib.md5(path.read_bytes()).hexdigest() == digest, relative
        assert not (app/'logs').exists() and not (app/'models').exists() and not (app/'cache').exists()
        assert not any(p.name == '__pycache__' for p in app.rglob('*'))
        assert (app/'config/model-source.json').is_file()
        assert (app/'scripts/setup_ui.py').is_file()
        assert (app/'scripts/app_setup.py').is_file()
        assert os.access(stage/'usr/bin/cosmos-camera-setup-host', os.X_OK)
        assert os.access(stage/'DEBIAN/preinst', os.X_OK)
        subprocess.run(['sh', '-n', str(stage/'DEBIAN/preinst')], check=True)
        metadata = subprocess.check_output(['dpkg-deb', '-f', str(args.package.resolve())], text=True)
        assert 'Architecture: arm64' in metadata
        for dependency in ('docker.io | docker-ce', 'nvidia-container-toolkit', 'policykit-1'):
            assert dependency in metadata
        assert '/home/seeed' not in (stage/'usr/bin/cosmos-camera').read_text()
        assert '/home/seeed' not in (stage/'usr/share/applications/cosmos-camera.desktop').read_text()
        subprocess.run(['desktop-file-validate', str(stage/'usr/share/applications/cosmos-camera.desktop')], check=True)
        before = sorted(str(p.relative_to(app)) for p in app.rglob('*'))
        for path in app.rglob('*'):
            path.chmod(0o555 if path.is_dir() else 0o444)
        app.chmod(0o555)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                   XDG_CONFIG_HOME=str(report_dir/'config'), XDG_STATE_HOME=str(report_dir/'state'),
                   XDG_CACHE_HOME=str(report_dir/'cache'),
                   COSMOS_TEST_APP_ROOT=str(app), COSMOS_TEST_MODEL_DIR=str(ROOT/'models/cosmos-download'))
        version = subprocess.check_output(['/usr/bin/python3', '-s', '-B', str(app/'scripts/camera_ui.py'), '--version'],
                                          env=env, text=True).strip()
        assert version == 'Cosmos Camera ' + (ROOT/'VERSION').read_text().strip()
        result.update(version=version, manifest_verified=True, clean_package=True, desktop_entry_valid=True)
        try:
            if args.gui:
                completed = subprocess.run(['/usr/bin/python3', '-s', '-B', str(ROOT/'tests/qt_camera_smoke.py')],
                                           env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           timeout=90)
                (report_dir/'gui-test.log').write_text(completed.stdout)
                print(completed.stdout, flush=True)
                completed.check_returncode()
                result['gui_with_system_dependencies'] = 'passed'
            assert before == sorted(str(p.relative_to(app)) for p in app.rglob('*'))
            result.update(status='passed', program_directory_unchanged=True)
        finally:
            # TemporaryDirectory needs directory write access for its own cleanup.
            app.chmod(0o755)
            for path in app.rglob('*'):
                if path.is_dir():
                    path.chmod(0o755)
    (report_dir/'package-report.json').write_text(json.dumps(result, indent=2))
    print('Package check: ' + str(report_dir/'package-report.json'))
    print(json.dumps(result))


if __name__ == '__main__':
    main()

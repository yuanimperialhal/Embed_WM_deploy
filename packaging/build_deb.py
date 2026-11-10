#!/usr/bin/env python3
"""Build a Jetson desktop package with first-run provisioning.

No sudo, system install, model download or container startup occurs here.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ('camera_ui.py', 'camera_ui_qt.py', 'camera_ui_backend.py', 'app_runtime.py',
           'app_diagnostics.py', 'camera_infer.py', 'camera_preview.py', 'serve.py',
           'run_single_image.py', 'bench_image.py', 'app_setup.py', 'setup_ui.py', 'setup_host.py')


def build(output_dir, revision, maintainer):
    version = (ROOT/'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('VERSION must be major.minor.patch, e.g. 0.1.0')
    if not re.fullmatch(r'[1-9][0-9]*', revision):
        raise ValueError('Revision must be a positive integer')
    if '\n' in maintainer or '\r' in maintainer:
        raise ValueError('Maintainer must be one line')
    package_version = f'{version}-{revision}'
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir/f'cosmos-camera_{package_version}_arm64.deb'
    with tempfile.TemporaryDirectory(prefix='cosmos-package-') as temporary:
        stage = Path(temporary)
        app = stage/'usr/lib/cosmos-camera'
        def copy(source, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            destination.chmod(0o644)
        for name in SCRIPTS:
            copy(ROOT/'scripts'/name, app/'scripts'/name)
        for name in ('cosmos-camera.svg', 'chevron-down.svg'):
            copy(ROOT/'assets'/name, app/'assets'/name)
        copy(ROOT/'VERSION', app/'VERSION')
        copy(ROOT/'config/model-source.json', app/'config/model-source.json')
        copy(ROOT/'THIRD_PARTY_NOTICES.md', stage/'usr/share/doc/cosmos-camera/copyright')
        copy(ROOT/'docs/installed-app-guide.md', app/'docs/camera-ui-guide.md')
        copy(ROOT/'docs/release-notes.md', stage/'usr/share/doc/cosmos-camera/release-notes.md')
        changelog = (f'cosmos-camera ({package_version}) stable; urgency=medium\n\n'
                     '  * Desktop camera assistant with pinned first-run provisioning.\n\n'
                     f' -- {maintainer}  Tue, 10 Nov 2026 12:00:00 +0800\n')
        (stage/'usr/share/doc/cosmos-camera/changelog.Debian.gz').write_bytes(
            gzip.compress(changelog.encode(), mtime=0))
        man = stage/'usr/share/man/man1'
        man.mkdir(parents=True, exist_ok=True)
        for name in ('cosmos-camera', 'cosmos-camera-setup-host'):
            (man/(name+'.1.gz')).write_bytes(gzip.compress((ROOT/'packaging/man'/f'{name}.1').read_bytes(), mtime=0))
        (app/'INSTALL_MODE').write_text('Managed by the package manager. User data lives outside this directory.\n')
        launcher = stage/'usr/bin/cosmos-camera'
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 -s -B /usr/lib/cosmos-camera/scripts/camera_ui.py "$@"\n')
        launcher.chmod(0o755)
        helper = stage/'usr/bin/cosmos-camera-setup-host'
        helper.write_text('#!/bin/sh\nexec /usr/bin/python3 -I -B /usr/lib/cosmos-camera/scripts/setup_host.py "$@"\n')
        helper.chmod(0o755)
        desktop = stage/'usr/share/applications/cosmos-camera.desktop'
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text('''[Desktop Entry]
Version=1.0
Type=Application
Name=Cosmos 相机助手
Comment=USB 相机实时预览与 Cosmos 视觉分析
Exec=/usr/bin/cosmos-camera
TryExec=/usr/bin/cosmos-camera
Icon=cosmos-camera
Terminal=false
Categories=AudioVideo;Video;
StartupNotify=true
StartupWMClass=cosmos-camera
''')
        copy(ROOT/'assets/cosmos-camera.svg', stage/'usr/share/icons/hicolor/scalable/apps/cosmos-camera.svg')
        validator = shutil.which('desktop-file-validate')
        if validator:
            subprocess.run([validator, str(desktop)], check=True)
        control_dir = stage/'DEBIAN'
        control_dir.mkdir()
        preinst = control_dir/'preinst'
        preinst.write_text((ROOT/'packaging/preinst').read_text())
        size = sum(p.stat().st_size for p in stage.rglob('*') if p.is_file())
        (control_dir/'control').write_text(f'''Package: cosmos-camera
Version: {package_version}
Section: video
Priority: optional
Architecture: arm64
Maintainer: {maintainer}
Installed-Size: {(size + 1023) // 1024}
Depends: ca-certificates, docker.io | docker-ce, nvidia-container-toolkit, policykit-1, python3 (>= 3.10), python3-pyqt5 (>= 5.15), libqt5svg5, python3-opencv, python3-pil, fonts-noto-cjk, nvidia-l4t-core (>= 36.4), nvidia-l4t-core (<< 36.5)
Description: Local Cosmos camera assistant for Jetson Linux
 Native Qt camera preview with streamed single-image visual analysis.
 For Ubuntu 22.04 / Jetson L4T R36.4.x (arm64).
 Includes a first-run wizard to download pinned model files and the vLLM
 image, verify checksums and configure user settings. Model access must
 be granted by its original author; weights are not redistributed.
''')
        sums = []
        for path in sorted((stage/'usr').rglob('*')):
            if path.is_file():
                sums.append(hashlib.md5(path.read_bytes()).hexdigest() + '  ' + path.relative_to(stage).as_posix())
        (control_dir/'md5sums').write_text('\n'.join(sums) + '\n')
        # Normalise permissions so a restrictive caller umask cannot hide app files.
        for path in stage.rglob('*'):
            path.chmod(0o755 if path.is_dir() or path in (launcher, helper, preinst) else 0o644)
        subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(stage), str(target)], check=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.deb.sha256').write_text(f'{digest}  {target.name}\n')
    manifest = dict(package=target.name, version=package_version, architecture='arm64',
                    bytes=target.stat().st_size, sha256=digest,
                    target='Ubuntu 22.04 / Jetson L4T R36.4.x',
                    reference_hardware='Jetson Orin / L4T R36.4.3',
                    clean_device_install_tested=False,
                    bundled=['application source', 'first-run wizard', 'model checksum manifest', 'icons', 'user guide'],
                    external=['authorized model download', 'pinned vLLM container'],
                    system_dependencies=['Docker', 'NVIDIA Container Toolkit', 'Qt', 'OpenCV'],
                    installed=False)
    target.with_suffix('.release.json').write_text(json.dumps(manifest, indent=2) + '\n')
    alias = output_dir/'cosmos-camera_latest_arm64.deb'
    shutil.copyfile(target, alias)
    alias.with_suffix('.deb.sha256').write_text(f'{digest}  {alias.name}\n')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'dist')
    parser.add_argument('--revision', default='1')
    release = json.loads((ROOT/'config/release.json').read_text())
    parser.add_argument('--maintainer', default=release['maintainer'])
    args = parser.parse_args()
    if not shutil.which('dpkg-deb'):
        parser.error('dpkg-deb is required (Ubuntu/Debian dpkg package)')
    target = build(args.output_dir.resolve(), args.revision, args.maintainer)
    print(f'Built: {target}\nSHA256: {target}.sha256\nThe package has not been installed.')


if __name__ == '__main__':
    main()

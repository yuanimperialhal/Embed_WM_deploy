#!/usr/bin/env python3
"""Download Qt/Python/OpenCV dependency closure without installing any packages."""
import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ['python3', 'python3-pyqt5', 'libqt5svg5', 'python3-opencv', 'python3-pil',
            'fonts-noto-cjk', 'ca-certificates', 'policykit-1']


def collect(destination):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    (destination/'partial').mkdir(exist_ok=True)
    status = destination.parent/'empty-dpkg-status'
    status.write_text('')
    command = ['apt-get', '--download-only', '--yes', '--no-install-recommends',
               '-o', 'Dir::State::status=' + str(status),
               '-o', 'Dir::Cache::archives=' + str(destination),
               '-o', 'Dir::Cache::pkgcache=', '-o', 'Dir::Cache::srcpkgcache=',
               '-o', 'Debug::NoLocking=1', 'install', *PACKAGES]
    log = destination.parent/'download-runtime.log'
    print('Downloading runtime dependencies only; system packages will not change.', flush=True)
    print('Download log: ' + str(log), flush=True)
    with log.open('w') as output:
        subprocess.run(command, stdout=output, stderr=subprocess.STDOUT,
                       env={**os.environ, 'LC_ALL': 'C'}, check=True)
    with (destination/'Packages').open('w') as output:
        subprocess.run(['dpkg-scanpackages', '--multiversion', '.', '/dev/null'], cwd=destination,
                       stdout=output, check=True)
    packages = list(destination.glob('*.deb'))
    print(f'Prepared {len(packages)} dependency packages, {sum(p.stat().st_size for p in packages)/1024**2:.1f} MiB', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'dist/offline-assets/host-debs')
    collect(parser.parse_args().output_dir)

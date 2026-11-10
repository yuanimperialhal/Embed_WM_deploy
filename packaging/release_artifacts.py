#!/usr/bin/env python3
"""Validate the release inputs, then build the public DEB and checksum files."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag')
    parser.add_argument('--repository', help='Require release configuration to match this GitHub repository')
    args = parser.parse_args()
    version = (ROOT/'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        parser.error('VERSION must be major.minor.patch')
    if args.tag and args.tag != 'v'+version:
        parser.error('Tag must match VERSION: v'+version)
    config = json.loads((ROOT/'config/release.json').read_text())
    if args.repository and args.repository != config['repository']:
        parser.error('Run packaging/configure_repository.py for this repository before tagging.')
    subprocess.run([sys.executable, str(ROOT/'packaging/build_deb.py'), '--maintainer', config['maintainer']], check=True)
    subprocess.run([sys.executable, str(ROOT/'tests/package_smoke.py'),
                    str(ROOT/f'dist/cosmos-camera_{version}-1_arm64.deb')], check=True)


if __name__ == '__main__':
    main()

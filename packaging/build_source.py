#!/usr/bin/env python3
"""Create an explicit source-only archive suitable for a new GitHub repository."""
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('scripts', 'assets', 'config', 'packaging', 'tests', 'docs', '.github')
FILES = ('README.md', 'VERSION', '.gitignore', 'THIRD_PARTY_NOTICES.md')


def main():
    version = (ROOT/'VERSION').read_text().strip()
    destination = ROOT/f'dist/cosmos-camera-{version}-source.tar.gz'
    destination.parent.mkdir(exist_ok=True)
    paths = [ROOT/name for name in FILES]
    for directory in DIRECTORIES:
        paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.partial'))
    with tarfile.open(destination, 'w:gz') as archive:
        for path in sorted(paths):
            if path.is_symlink():
                raise ValueError('Source archive cannot contain symlinks: ' + str(path))
            archive.add(path, arcname='cosmos-camera/'+str(path.relative_to(ROOT)), recursive=False)
    print(destination)


if __name__ == '__main__':
    main()

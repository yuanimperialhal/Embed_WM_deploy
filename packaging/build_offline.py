#!/usr/bin/env python3
"""Build one executable .run with app, model, Docker image and runtime debs."""
import argparse
from contextlib import contextmanager
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from app_runtime import VERSION, model_files_error
from run_single_image import IMAGE
from build_deb import build as build_deb
from collect_runtime_debs import collect
from offline_common import sha256_file, validate_image_archive


@contextmanager
def compressor(output):
    """Use available CPU cores; the result remains standard gzip on all targets."""
    pigz = shutil.which('pigz')
    if not pigz:
        with gzip.GzipFile(fileobj=output, filename='', mode='wb', compresslevel=1, mtime=0) as stream:
            yield stream
        return
    output.flush()
    process = subprocess.Popen([pigz, '-1', '-n', '-p', str(min(4, os.cpu_count() or 1))],
                               stdin=subprocess.PIPE, stdout=output)
    try:
        yield process.stdin
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError('Parallel gzip compression failed')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if not process.stdin.closed:
            process.stdin.close()


def create_bundle(sources, metadata, target):
    """sources maps safe archive names to Paths or bytes; also used by tests."""
    template = (ROOT/'packaging/offline_bootstrap.py.in').read_text()
    header = template.replace('__METADATA__', repr(metadata)).replace('__PAYLOAD_SHA256__', '0'*64).encode()
    offset = header.index(b'PAYLOAD_SHA256 = "') + len(b'PAYLOAD_SHA256 = "')
    partial = target.with_name(target.name+'.partial')
    try:
        with partial.open('w+b') as output:
            output.write(header)
            with compressor(output) as compressed:
                # The inference image exceeds USTAR's 8 GiB per-file limit.
                with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as archive:
                    for name, source in sources.items():
                        size = len(source) if isinstance(source, bytes) else source.stat().st_size
                        info = tarfile.TarInfo(name)
                        info.size, info.mode, info.uid, info.gid, info.mtime = size, 0o644, 0, 0, 0
                        if isinstance(source, bytes):
                            archive.addfile(info, io.BytesIO(source))
                        else:
                            print(f'Packing {name} ({size/1024**2:.1f} MiB)', flush=True)
                            with source.open('rb') as stream:
                                archive.addfile(info, stream)
            output.flush()
            output.seek(len(header))
            digest = hashlib.sha256()
            for block in iter(lambda: output.read(4*1024*1024), b''):
                digest.update(block)
            output.seek(offset)
            output.write(digest.hexdigest().encode())
            output.flush()
            os.fsync(output.fileno())
        partial.chmod(0o755)
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, default=ROOT/'models/cosmos-download')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'dist')
    parser.add_argument('--assets-dir', type=Path, default=ROOT/'dist/offline-assets')
    args = parser.parse_args()
    error = model_files_error(args.model_dir)
    if error:
        parser.error(error)
    assets = args.assets_dir.resolve()
    assets.mkdir(parents=True, exist_ok=True)
    destination = args.output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', IMAGE], text=True))[0]
    if image['Architecture'] != 'arm64' or image['Os'] != 'linux':
        parser.error('The pinned image must be linux/arm64')
    model_paths = sorted(p for p in args.model_dir.iterdir() if p.is_file() and not p.name.startswith('.'))
    if any(p.is_symlink() for p in model_paths):
        parser.error('Use a materialised model folder, not symlinks into a download cache')
    model_bytes = sum(p.stat().st_size for p in model_paths)
    cached_image = assets/'vllm-image.tar'
    # Conservative allowance for output plus an uncached docker export.
    need = model_bytes + image['Size'] + 3*1024**3
    if not cached_image.exists():
        need += image['Size']
    if shutil.disk_usage(destination).free < need:
        parser.error(f'Insufficient free space; need approximately {need/1024**3:.1f} GiB')
    if not cached_image.exists():
        temporary = cached_image.with_suffix('.tar.partial')
        subprocess.run(['docker', 'image', 'save', '-o', str(temporary), IMAGE], check=True)
        temporary.replace(cached_image)
    ids = validate_image_archive(cached_image, image['Id'])
    dependencies = assets/'host-debs'
    if not (dependencies/'Packages').is_file():
        collect(dependencies)
    package = build_deb(destination, '1', 'Local developer <local@localhost>')
    sources = {'install.py': ROOT/'packaging/offline_install.py',
               'offline_common.py': ROOT/'packaging/offline_common.py',
               'README.md': ROOT/'docs/offline-installer-guide.md',
               'app/'+package.name: package}
    sources.update({'model/'+p.name: p for p in model_paths})
    sources['image/vllm.tar'] = cached_image
    for path in sorted(dependencies.glob('*.deb')):
        arch = subprocess.check_output(['dpkg-deb', '-f', str(path), 'Architecture'], text=True).strip()
        if arch not in ('arm64', 'all'):
            parser.error('Wrong dependency architecture: '+str(path))
        sources['host-debs/'+path.name] = path
    if len(list(dependencies.glob('*.deb'))) < 6:
        parser.error('Runtime dependency collection is incomplete')
    sources['host-debs/Packages'] = dependencies/'Packages'
    print('Calculating payload checksums…', flush=True)
    records = [dict(path=name, bytes=source.stat().st_size, sha256=sha256_file(source))
               for name, source in sources.items()]
    model_records = [r for r in records if r['path'].startswith('model/')]
    model_id = 'cosmos-' + hashlib.sha256(json.dumps(model_records, sort_keys=True).encode()).hexdigest()[:16]
    manifest = dict(schema_version=1, version=VERSION, application='app/'+package.name,
                    image_reference=IMAGE, image_ids=ids, image_unpacked_bytes=image['Size'],
                    model_id=model_id, model_bytes=model_bytes, files=records)
    manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2)+'\n').encode()
    sources['manifest.json'] = manifest_data
    metadata = dict(name='Cosmos 相机助手离线安装器', version=VERSION,
                    target='Jetson arm64 / Ubuntu 22.04 / L4T R36.4.3',
                    includes=['Qt application', 'Cosmos model', 'vLLM image', 'Python/Qt/OpenCV debs'],
                    requires=['Matching JetPack and NVIDIA drivers', 'Local Docker with NVIDIA runtime',
                              'Desktop session; normal user has Docker and camera access'],
                    dependency_packages=len(list(dependencies.glob('*.deb'))),
                    file_count=len(sources), unpacked_bytes=sum(r['bytes'] for r in records)+len(manifest_data))
    target = destination/f'CosmosCamera-{VERSION}-jetson-arm64.run'
    digest = create_bundle(sources, metadata, target)
    checksum = sha256_file(target)
    target.with_suffix('.run.sha256').write_text(checksum+'  '+target.name+'\n')
    target.with_suffix('.manifest.json').write_bytes(manifest_data)
    target.with_suffix('.release.json').write_text(json.dumps(dict(metadata, payload_sha256=digest,
        sha256=checksum, bytes=target.stat().st_size, system_install_tested=False), ensure_ascii=False, indent=2)+'\n')
    print(f'Built {target}\nSize: {target.stat().st_size/1024**3:.2f} GiB\nNo software was installed.', flush=True)


if __name__ == '__main__':
    main()

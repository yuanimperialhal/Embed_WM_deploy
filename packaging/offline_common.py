"""Small standard-library helpers copied into the offline installer."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

CHUNK = 4 * 1024 * 1024


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(p in ('..', '.') for p in path.parts) or '\\' in name:
        raise ValueError('Unsafe archive path: ' + name)
    return path


def verify_files(root, manifest):
    root = Path(root).resolve()
    seen = set()
    for record in manifest['files']:
        relative = safe_member(record['path'])
        if str(relative) in seen:
            raise ValueError('Duplicate file in manifest: ' + str(relative))
        seen.add(str(relative))
        path = root.joinpath(*relative.parts)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError('Missing or unsafe payload file: ' + str(relative))
        if path.stat().st_size != record['bytes'] or sha256_file(path) != record['sha256']:
            raise ValueError('Payload checksum mismatch: ' + str(relative))
    return True


def validate_image_archive(path, expected_id):
    with tarfile.open(path, 'r:') as archive:
        entries = json.load(archive.extractfile('manifest.json'))
        if len(entries) != 1:
            raise ValueError('The archive must contain exactly one image')
        config_name = entries[0]['Config']
        safe_member(config_name)
        config = archive.extractfile(config_name).read()
        config_id = 'sha256:' + hashlib.sha256(config).hexdigest()
        details = json.loads(config)
        if details.get('architecture') != 'arm64' or details.get('os') != 'linux':
            raise ValueError('Exported image config does not match the expected linux/arm64 image')
        if config_id != expected_id:
            # Docker's containerd store uses manifest IDs; the classic store uses
            # config IDs. Verify the relationship and retain both for import.
            index = json.load(archive.extractfile('index.json'))
            descriptor = next((d for d in index['manifests'] if d['digest'] == expected_id), None)
            if descriptor is None:
                raise ValueError('Expected manifest missing from OCI index')
            raw = archive.extractfile('blobs/sha256/'+expected_id.split(':')[1]).read()
            if 'sha256:'+hashlib.sha256(raw).hexdigest() != expected_id:
                raise ValueError('OCI manifest checksum mismatch')
            if json.loads(raw)['config']['digest'] != config_id:
                raise ValueError('OCI manifest refers to a different config')
    return list(dict.fromkeys([expected_id, config_id]))

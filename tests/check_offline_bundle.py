#!/usr/bin/env python3
"""Stream-check a real .run and every embedded file without installing/extracting."""
import argparse
import ast
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tarfile
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'packaging'))
from offline_common import CHUNK, safe_member


class DigestReader:
    def __init__(self, stream, header):
        self.stream = stream
        self.payload = hashlib.sha256()
        self.whole = hashlib.sha256(header)

    def read(self, size=-1):
        data = self.stream.read(size)
        self.payload.update(data)
        self.whole.update(data)
        return data


def check(path):
    records = {}
    manifest = None
    with path.open('rb') as raw:
        header = bytearray()
        while len(header) < 1024*1024:
            line = raw.readline(1024*1024)
            if not line:
                raise ValueError('Missing payload marker')
            header.extend(line)
            if line == b'__COSMOS_PAYLOAD__\n':
                break
        else:
            raise ValueError('Invalid or oversized installer header')
        declarations = {}
        for line in header.decode().splitlines():
            if line.startswith(('METADATA = ', 'PAYLOAD_SHA256 = ')):
                name, value = line.split(' = ', 1)
                declarations[name] = ast.literal_eval(value)
        reader = DigestReader(raw, header)
        with gzip.GzipFile(fileobj=reader, mode='rb') as compressed:
            with tarfile.open(fileobj=compressed, mode='r|') as archive:
                for member in archive:
                    name = str(safe_member(member.name))
                    if not member.isfile() or name in records:
                        raise ValueError('Duplicate or unsupported member: '+name)
                    digest = hashlib.sha256()
                    count = 0
                    chunks = [] if name == 'manifest.json' else None
                    with archive.extractfile(member) as source:
                        for block in iter(lambda: source.read(CHUNK), b''):
                            count += len(block)
                            digest.update(block)
                            if chunks is not None:
                                if count > 4*1024*1024:
                                    raise ValueError('Oversized manifest')
                                chunks.append(block)
                    if count != member.size:
                        raise ValueError('Truncated member: '+name)
                    records[name] = dict(bytes=count, sha256=digest.hexdigest())
                    if chunks is not None:
                        manifest = json.loads(b''.join(chunks))
                    if name.startswith(('model/model.', 'image/')):
                        print('Verified stream: '+name, flush=True)
            # Consume tar padding and gzip footer, including CRC validation.
            while compressed.read(CHUNK):
                pass
        while reader.read(CHUNK):
            pass
        if reader.payload.hexdigest() != declarations['PAYLOAD_SHA256']:
            raise ValueError('Compressed payload checksum mismatch')
    metadata = declarations['METADATA']
    if len(records) != metadata['file_count'] or sum(r['bytes'] for r in records.values()) != metadata['unpacked_bytes']:
        raise ValueError('Payload file count or size mismatch')
    if manifest is None or manifest.get('schema_version') != 1:
        raise ValueError('Missing or unsupported manifest')
    declared = set()
    for record in manifest['files']:
        name = str(safe_member(record['path']))
        if name in declared or records.get(name) != dict(bytes=record['bytes'], sha256=record['sha256']):
            raise ValueError('Manifest checksum mismatch: '+name)
        declared.add(name)
    if declared != set(records) - {'manifest.json'}:
        raise ValueError('Undeclared or missing payload file')
    sidecar = path.with_suffix('.run.sha256')
    if sidecar.is_file() and sidecar.read_text().split()[0] != reader.whole.hexdigest():
        raise ValueError('Whole-file checksum mismatch')
    return dict(checked_at=datetime.now(timezone.utc).isoformat(), file=str(path),
                bytes=path.stat().st_size, files_verified=len(records),
                sha256=reader.whole.hexdigest(), payload_sha256=reader.payload.hexdigest(),
                manifest_matches=True, gzip_crc_valid=True, extracted=False,
                system_install_tested=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    args = parser.parse_args()
    path = args.bundle.resolve()
    result = check(path)
    report = path.with_suffix('.verification.json')
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('Report: '+str(report))

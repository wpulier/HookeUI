#!/usr/bin/env python3
"""Fingerprint the assembled runtime, including built Studio assets and build identity.

Run after adding generated assets. packaged.json is excluded to avoid hashing itself;
its identity is part of the digest. The source privacy report remains intact.
"""
import hashlib
import json
import sys
from pathlib import Path


def finalize(snapshot: Path) -> str:
    tree = snapshot / 'tree'
    packaged_path = tree / 'packaged.json'
    packaged = json.loads(packaged_path.read_text())
    files = []
    for path in sorted(tree.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Snapshot contains a symlink: {path.relative_to(tree)}')
        if not path.is_file() or path == packaged_path:
            continue
        files.append({'path': path.relative_to(tree).as_posix(),
                      'mode': '100755' if path.stat().st_mode & 0o111 else '100644',
                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    content = hashlib.sha256(json.dumps({'identity': packaged['identity'], 'files': files},
                                       sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    packaged['contentHash'] = content
    packaged_path.write_text(json.dumps(packaged, indent=1) + '\n')
    report_path = snapshot / 'report.json'
    report = json.loads(report_path.read_text())
    report.update(files=len(files), contentHash=content, assembledRuntime=True)
    report_path.write_text(json.dumps(report, indent=1) + '\n')
    (snapshot / 'manifest.json').write_text(json.dumps(files, indent=1) + '\n')
    return content


if __name__ == '__main__':
    print(finalize(Path(sys.argv[1])))

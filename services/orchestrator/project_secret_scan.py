"""Find secrets and personal paths in the record layer before anything leaves the Mac (ADR 0028).

A finding names the file, line and kind, never the matched value. Secrets block a push or publication; home-directory
paths are shown for the owner to judge, because accepted records cannot be edited without changing their hashes.
"""
from __future__ import annotations

import re
from pathlib import Path

from .report import SECRETS

TEXT = ('.json', '.jsonl', '.md', '.txt', '.csv', '.tsv', '.toml', '.yaml', '.yml', '.py', '.cff', '.xml', '.html')
MAX_SCAN = 32 << 20
MAX_FINDINGS = 200
EXTRA = re.compile(r'(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk_live_[A-Za-z0-9]{16,}|'
                   r'eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}|'
                   r'"(?:api[_-]?key|access[_-]?token|secret|password)"\s*:\s*"[^"\s]{12,}")', re.IGNORECASE)
HOME = re.compile(r'(?:/Users|/home)/(?!Shared/)[A-Za-z0-9._\-]+/')


def _configured():
    try:
        from .report import _configured as configured
        return [v for v in configured() if isinstance(v, str) and len(v) >= 12]
    except Exception:
        return []


def scan(folder: Path, entries, *, check=lambda: None):
    """Findings over the text members of `entries` (snapshot entries) under `folder`."""
    folder = Path(folder)
    values = _configured()
    findings = []
    for entry in entries:
        check()
        path = entry['path']
        if not path.lower().endswith(TEXT) or entry['bytes'] > MAX_SCAN:
            continue
        try:
            text = (folder / path).read_bytes().decode('utf-8', 'replace')
        except OSError:
            findings.append({'path': path, 'line': 0, 'kind': 'unreadable', 'blocks': True})
            continue
        for number, line in enumerate(text.splitlines(), 1):
            kinds = []
            if SECRETS.search(line) or EXTRA.search(line):
                kinds.append('secret')
            if values and any(v in line for v in values):
                kinds.append('configured-key')
            if HOME.search(line):
                kinds.append('home-path')
            for kind in kinds:
                findings.append({'path': path, 'line': number, 'kind': kind, 'blocks': kind != 'home-path'})
                if len(findings) >= MAX_FINDINGS:
                    findings.append({'path': path, 'line': number, 'kind': 'more-not-listed', 'blocks': True})
                    return findings
    return findings


def blocking(findings):
    return [f for f in findings if f['blocks']]


def said(findings) -> str:
    """Plain words for the first few findings, naming places only."""
    if not findings:
        return 'No secrets or personal paths were found.'
    words = {'secret': 'what looks like a key or password', 'configured-key': 'one of this Mac’s keys',
             'home-path': 'a path inside a home folder', 'unreadable': 'a file that could not be read',
             'more-not-listed': 'more findings'}
    shown = [f"{words.get(f['kind'], f['kind'])} in {f['path']} line {f['line']}" for f in findings[:3]]
    more = f' and {len(findings) - 3} more' if len(findings) > 3 else ''
    return 'Found ' + '; '.join(shown) + more + '.'

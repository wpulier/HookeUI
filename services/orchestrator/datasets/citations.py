"""Bounded, read-only dataset model provenance. Settings and free text are not evidence of use."""
from __future__ import annotations

import json
from pathlib import Path

from . import manifest


def recorded_models(folder: Path, *, byte_limit: int = 16 * 1024 * 1024, line_limit: int = 100_000) -> dict:
    from .. import licences, options

    declared = {}
    for group in options.GROUPS.values():
        for option in group.values():
            facts = [{"title": licences.COMPONENTS[u].what, "url": licences.COMPONENTS[u].url,
                      "licence": licences.COMPONENTS[u].licence} for u in option.uses if u in licences.COMPONENTS]
            for key in (option.id, option.spec):
                if key:
                    declared[key] = facts
    found: dict[str, dict] = {}
    path = folder / manifest.EVENTS
    try:
        initial = path.stat()
        consumed = lines = invalid = 0
        with path.open('rb') as source:
            while lines < line_limit and consumed < byte_limit:
                raw = source.readline(min(1024 * 1024, byte_limit - consumed) + 1)
                if not raw:
                    break
                consumed += len(raw)
                if consumed > byte_limit or not raw.endswith(b'\n'):
                    invalid += 1
                    break  # torn or oversized line: explicit incomplete evidence
                lines += 1
                try:
                    event = json.loads(raw)
                except (ValueError, UnicodeError):
                    invalid += 1
                    continue
                if not isinstance(event, dict) or event.get('stream') in {'settings', 'dataset'}:
                    continue
                record = event.get('record')
                if not isinstance(record, dict):
                    continue
                # These are the recorded producer fields, not options, prompts or model-authored text.
                model = record.get('model')
                if not isinstance(model, str) or not model.strip() or len(model) > 200:
                    continue
                row = found.setdefault(model, {"model": model, "events": 0, "sources": declared.get(model, []),
                                               "version": None, "citation": None})
                row['events'] += 1
            complete = consumed >= initial.st_size and invalid == 0 and path.stat().st_mtime_ns == initial.st_mtime_ns
        return {"models": sorted(found.values(), key=lambda item: item['model']), "complete": complete,
                "linesRead": lines, "bytesRead": consumed, "invalidLines": invalid,
                "qualification": "Only explicit recorded model identifiers; unrecorded use, exact weight versions and formal citations remain unknown."}
    except OSError:
        return {"models": [], "complete": False, "linesRead": 0, "bytesRead": 0, "invalidLines": 0,
                "qualification": "Dataset events are unavailable; model usage is unknown."}

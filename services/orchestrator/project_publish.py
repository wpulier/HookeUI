"""Publish a Project: a reviewed, filtered snapshot sent as one version (ADR 0028, format v2 section 8).

A publication keeps the Project's origin and every accepted document byte for byte. Its journals are cut to their
origin lines and the lines that list their results (project_inherited), so conversations never leave; a person's
notes on results are published text, shown in the review and read by the secret scan. Research transcripts,
connector operations and restoration receipts are left out unless chosen. The owner reviews the exact members, a secret scan must pass, and the owner signs off on
rights, licence and the absence of identifiable human data. The review digest binds what is sent.

Imported images are cleaned of personal metadata for the publication by default (project_publish_clean): the
publication holds a copy with the same pixels and leaves the original's bytes out, and the Project keeps its original.
"""
from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import re
import shutil

from .day import ownership
from .day.sid import is_sid
from .project_folder_copy import copy_snapshot
from .project_readme import citation, licence_notice, readme, ro_crate
from .project_remote import Remote, push_sealed, sealed_manifest
from .project_remote_layers import RemoteRefused, canonical, classify, sha256
from .project_repository import verify_owner
from .project_roots import PARTS, ProjectRoot
from .project_inherited import notes, published
from .project_metadata_scan import scan as metadata_scan
from .project_publish_clean import clean as clean_images
from .project_secret_scan import blocking, scan

REVIEW_FORMAT = 'hooke-publication-review-v1'
LICENCES = {'CC-BY-4.0': 'Creative Commons Attribution 4.0', 'CC0-1.0': 'Creative Commons Zero 1.0'}
DEFAULT_LICENCE = 'CC-BY-4.0'
LEFT_OUT = ('history/operations', 'history/restorations', 'exports')


def _copy_tree(source: Path, target: Path):
    """Verbatim copy of retained files; links and special files refuse, as in sealing."""
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        destination = target / relative
        if path.is_symlink():
            raise RemoteRefused('A Project holds a link; collect it before publishing')
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            os.chmod(destination, 0o600)


def _filtered(full: ProjectRoot, target: Path, *, include_transcripts: bool):
    """An unsealed Project folder holding only what a publication carries."""
    target.mkdir(mode=0o700)
    for name in ('project.json', 'README.md'):
        shutil.copy2(full.path / name, target / name)
        os.chmod(target / name, 0o600)
    for part in PARTS:
        (target / part).mkdir(mode=0o700)
    sessions = full.path / 'history' / 'sessions'
    journals, results, said = 0, 0, []
    for folder in sorted(sessions.iterdir()) if sessions.is_dir() else ():
        if not folder.is_dir() or not is_sid(folder.name):
            continue
        owner = ownership.retained_owner(sessions, folder.name)
        if owner is None:
            continue
        out = target / 'history' / 'sessions' / folder.name
        out.mkdir(parents=True)
        listed = published(folder / 'journal.jsonl')  # the lines that list its results; no conversation
        (out / 'journal.jsonl').write_bytes(ownership.journal_bytes(owner) + listed)
        journals += 1
        results += listed.count(b'\n')
        said.extend({'session': folder.name, **note} for note in notes(listed.splitlines()))
    artifacts = full.path / 'artifacts'
    left_out = {'transcripts': 0, 'results': results, 'notes': said}
    for section in sorted(artifacts.iterdir()):
        if section.name == 'research' and not include_transcripts:
            left_out['transcripts'] = sum(1 for p in section.rglob('*') if p.is_file())
            continue
        if section.is_dir():
            _copy_tree(section, target / 'artifacts' / section.name)
    for part in ('inputs', 'workspace'):
        _copy_tree(full.path / part, target / part)
    for section in ('history/sessions', 'artifacts/records'):
        (target / section).mkdir(parents=True, exist_ok=True)
        (target / section / '.bundle-visibility.lock').write_bytes(b'')
    return journals, left_out


def prepare(seal, staging: Path, *, include_transcripts=False, licence=DEFAULT_LICENCE, clean_metadata=True,
            check=lambda: None):
    """Build and seal the publication in `staging` (a new folder) and return its review for the owner.

    `seal(destination)` makes a sealed snapshot of the Project being published: `copy_snapshot` for an inactive
    folder, or `copy_owned_snapshot` under this host's lease. Nothing leaves the Mac here. With `clean_metadata` (the
    default) imported images go cleaned of personal metadata.
    """
    if licence not in LICENCES:
        raise RemoteRefused('Choose a licence Hooke can record: ' + ', '.join(LICENCES))
    staging = Path(staging)
    staging.mkdir(mode=0o700)
    full = seal(staging / 'full')
    full = full if isinstance(full, ProjectRoot) else ProjectRoot.open(full)
    owner = verify_owner(full)
    journals, left_out = _filtered(full, staging / 'filtered', include_transcripts=include_transcripts)
    cleaned, clean_findings = clean_images(staging / 'filtered', staging / 'cleaning', apply=bool(clean_metadata),
                                           check=check)
    shutil.rmtree(staging / 'cleaning', ignore_errors=True)
    publication = copy_snapshot(ProjectRoot.open(staging / 'filtered'), staging / 'publication', check=check)
    root, raw, manifest = sealed_manifest(publication.path)
    record, bulk = classify(manifest)
    findings = scan(root.path, record, check=check) + metadata_scan(root.path) + clean_findings
    full_raw = (full.path / 'snapshot.json').read_bytes()
    full_manifest = json.loads(full_raw)
    kept = {e['path'] for e in manifest['entries']}
    review = {
        'format': REVIEW_FORMAT, 'origin': root.origin, 'name': root.name, 'licence': licence,
        'licenceName': LICENCES[licence], 'snapshotSha256': sha256(raw),
        'members': manifest['entries'], 'bytes': sum(e['bytes'] for e in manifest['entries']),
        'recordFiles': len(record), 'largeFiles': len(bulk),
        'leftOut': {'conversations': f'{journals} journal(s) reduced to their identity line'
                                     + (f" and {left_out['results']} line(s) that list results" if left_out['results'] else ''),
                    'transcripts': left_out['transcripts'] if not include_transcripts else 0,
                    'otherFiles': sorted({e['path'] for e in full_manifest['entries']} - kept
                                         - {e['path'] for e in full_manifest['entries'] if e['path'].startswith('history/sessions/')})[:200]},
        # The person's own words that are published: their notes on results, read by the secret scan with the rest.
        'publishedText': left_out['notes'][:200], 'resultLines': left_out['results'],
        'includeTranscripts': bool(include_transcripts),
        # Imported images whose publication copy has the same pixels and none of the personal metadata named.
        'cleanMetadata': bool(clean_metadata), 'cleaned': cleaned[:200], 'cleanedFiles': len(cleaned),
        'findings': findings, 'blocked': bool(blocking(findings)),
        'originDigest': owner['originDigest'],
    }
    body = canonical(review)
    (staging / 'review.json').write_bytes(body)
    shutil.rmtree(staging / 'filtered')
    return {**review, 'reviewDigest': sha256(body), 'staging': str(staging)}


def signoff_problem(signoff) -> str | None:
    if type(signoff) is not dict:
        return 'Sign off before publishing'
    if signoff.get('rights') is not True:
        return 'Confirm you have the right to share everything listed'
    if signoff.get('noIdentifiableHumanData') is not True:
        return 'Confirm the files hold no identifiable human data, or that release is permitted'
    if signoff.get('permanent') is not True:
        return 'Confirm you understand a publication can be withdrawn from listings but not erased'
    author = signoff.get('author')
    if author is not None and (not isinstance(author, str) or not 0 < len(author.strip()) <= 200):
        return 'Give the author as a name of at most 200 characters, or leave it out'
    return None


def publish(staging: Path, *, review_digest: str, signoff: dict, remote: Remote, parent=None, replaces=None,
            check=lambda: None):
    """Send the reviewed publication. Returns the push receipt plus the journal event to record.

    `parent` is the previous publication's commit, if any. `replaces` is the placeholder commit of a repository
    Hooke has just created for a first publication; it is replaced, never kept as history.
    """
    staging = Path(staging)
    body = (staging / 'review.json').read_bytes()
    if sha256(body) != review_digest:
        raise RemoteRefused('The publication changed after it was reviewed; review it again')
    review = json.loads(body)
    problem = signoff_problem(signoff)
    if problem:
        raise RemoteRefused(problem)
    if review['blocked']:
        raise RemoteRefused('Resolve the findings in the review before publishing')
    publication = staging / 'publication'
    _, raw, _ = sealed_manifest(publication)
    if sha256(raw) != review['snapshotSha256']:
        raise RemoteRefused('The publication changed after it was reviewed; review it again')
    author = (signoff.get('author') or '').strip() or None
    today = date.today().isoformat()
    documents = _documents(publication)
    root_files = {
        'LICENSE': licence_notice(review['licence'], review['name']).encode(),
        'CITATION.cff': citation(review['name'], review['licence'], author, today, review['origin']).encode(),
        'ro-crate-metadata.json': ro_crate(review['name'], review['licence'], author, today, documents).encode(),
    }
    text = readme(review['name'], review['licence'], review['licenceName'], documents, review, today).encode()
    if replaces is not None and parent is not None:
        raise RemoteRefused('A later publication chains onto the last one; it never replaces a repository’s start')
    receipt = push_sealed(publication, remote, parent=parent, summary=f'Publish {review["name"]}', readme=text,
                          root_files=root_files, expected_head=replaces if replaces is not None else parent,
                          check=check)
    event = {'type': 'project-published', 'destination': remote.describe(), 'commit': receipt['commit'],
             'parent': parent, 'snapshotSha256': receipt['snapshotSha256'], 'reviewDigest': review_digest,
             'licence': review['licence'], 'includeTranscripts': review['includeTranscripts']}
    return {**receipt, 'event': event}


def _documents(publication: Path):
    """Title and kind of each accepted document, newest revision, for the README and RO-Crate."""
    records = publication / 'artifacts' / 'records'
    out = []
    for folder in sorted(records.iterdir()) if records.is_dir() else ():
        if not re.fullmatch(r'p[0-9a-f]{32}', folder.name):
            continue
        try:
            head = json.loads((folder / 'head.json').read_text())
            latest = json.loads((folder / 'revisions' / f'{head["revisions"][-1]}.json').read_text())
            document = latest['document']
            out.append({'id': folder.name, 'kind': str(document.get('kind', ''))[:60],
                        'title': str(document.get('title', ''))[:200], 'revisions': len(head['revisions'])})
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            out.append({'id': folder.name, 'kind': 'unreadable', 'title': '', 'revisions': 0})
    return out


def last_publication(events, destination: dict):
    """The commit of this Project's last publication to `destination`, from its journal events."""
    last = None
    for event in events:
        if event.get('type') == 'project-published' and event.get('destination') == destination:
            last = event.get('commit')
    return last

"""Sample data that installed readers declare (napari's sample_data), opened as ordinary retained sources.

A sample is metadata on a format-3 reader declaration: an id, a name, a pinned https file with its exact
SHA-256 and size, and optionally its licence. Listing reads declarations only: nothing is imported, installed,
downloaded or run. Opening one is an ordinary `scientific_url_intake.Download` with the declared digest as the
expected digest, so the same untrusted-web rules apply and bytes that differ from the declaration are refused
before anything is kept. The receipt names the declaring reader and sample, so the source says what it is.
A sample is data for trying a reader; it is not a reference result and qualifies nothing.
"""
from __future__ import annotations

import os

from gpu_worker import bundle_contract, manifests as catalog
from . import channels
from .scientific_readers import inexact
from .scientific_url_intake import Download, RemoteRefused


def listed(environ=os.environ):
    """Every declared sample, with the reader that declares it and whether that reader keeps its facts exact."""
    rows = []
    for owner in {**catalog.manifests(), **catalog.mine(environ)}.values():
        if owner.runner != 'python-env' or not owner.members:
            continue
        trusted = channels.trusted(owner, environ)
        try:
            members = bundle_contract.parse_members(list(owner.members), owner)
        except ValueError:
            continue  # an incomplete contribution never hides another integration's samples
        for member in members:
            reader = member.reader
            if not reader or reader.get('format') != 3:
                continue
            for sample in reader.get('samples', ()):
                rows.append({'capability': bundle_contract.capability_key(owner.key, member.id), 'reader': member.name,
                             'owner': owner.key, 'id': sample['id'], 'name': sample['name'], 'url': sample['url'],
                             'sha256': sample['sha256'], 'size': sample['size'], 'licence': sample.get('licence', ''),
                             'readerReason': inexact(reader), 'canOpen': trusted,
                             'reason': '' if trusted else 'Allow this catalog’s code in Models before using its samples',
                             'scope': 'Declared sample data; opening it downloads its pinned bytes and qualifies nothing'})
    return rows


def download(store, session, request, capability, sample, environ=os.environ):
    """The intake owner for one declared sample; its caller runs and closes it like any download."""
    row = next((r for r in listed(environ) if r['capability'] == capability and r['id'] == sample), None)
    if row is None:
        raise RemoteRefused('This sample is not declared by an installed reader.', status=404,
                            next='check the samples again; the integration may have changed or been removed')
    if not row['canOpen']:
        raise RemoteRefused(row['reason'] + '.', status=403, next='allow the catalog in Models, then choose the sample again')
    return Download(store, session, request, row['url'], sha256=row['sha256'], size=row['size'],
                    sample={'capability': capability, 'id': sample, 'name': row['name'], 'licence': row['licence']})

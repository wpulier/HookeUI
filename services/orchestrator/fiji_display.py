"""Reviewed Fiji disk facts for display, collected by the existing off-loop observer.

Execution, installation and generation selection keep calling fiji_generations/fiji_runner
directly. This facade cannot authorize them. Contributed Fiji entries retain their separate
live permission and immutable activation checks; they are not cached here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import env_observations, fiji_generations as live, fiji_runner

root = live.root


@dataclass
class Facts:
    current: int | None = None
    generations: list = field(default_factory=list)
    installed: dict = field(default_factory=dict)
    proofs: dict = field(default_factory=dict)
    identities: dict = field(default_factory=dict)


def key(environ):
    return ('reviewed-fiji', str(root(environ)))


def facts(environ):
    return env_observations.display_fact(key(environ), Facts())


def collect(environ):
    from gpu_worker import manifests as catalog
    from . import fiji_bindings

    try:
        n = live.current(environ)
        rows = live.generations(environ)
    except (OSError, ValueError, TypeError, AttributeError):
        return Facts()  # unreadable installation cannot retain an older displayed approval
    unreadable = 'Its Fiji setup record could not be read. Check requirements and retry setup.'
    for index, row in enumerate(rows):
        if 'receipt' not in row:
            continue
        r = row['receipt']
        if (not isinstance(r, dict) or not isinstance(r.get('proofs', {}), dict)
                or not isinstance(r.get('files', []), list) or not isinstance(r.get('sites', []), list)
                or any(not isinstance(site, dict) or not isinstance(site.get('name'), str)
                       for site in r.get('sites', []))):
            rows[index] = {'n': row['n'], 'state': 'failed', 'why': unreadable}
    valid_current = any(row['n'] == n and 'receipt' in row for row in rows)
    damaged_current = n is not None and not valid_current
    if damaged_current:
        n = None
    installed, proofs, identities = {}, {}, {}
    for m in catalog.manifests().values():
        if m.runner != 'fiji' or m.namespace:
            continue
        identities[m.key] = repr(m)
        if damaged_current:
            installed[repr(m)] = None
            proofs[m.key] = unreadable
            continue
        try:
            installed[repr(m)] = fiji_runner.in_use(m, environ)
            proofs[m.key] = live.proven(m.key, environ, n=n)
        except (OSError, ValueError, TypeError, AttributeError):
            installed[repr(m)] = None
            proofs[m.key] = 'Its Fiji proof could not be read. Check requirements and retry its check.'
    # The legacy reviewed binding is not a function-folder declaration.
    if fiji_bindings.BINDING not in proofs:
        try:
            proofs[fiji_bindings.BINDING] = live.proven(fiji_bindings.BINDING, environ, n=n)
        except (OSError, ValueError, TypeError, AttributeError):
            proofs[fiji_bindings.BINDING] = 'Its Fiji proof could not be read. Check requirements and retry its check.'
    # A publication/rollback may happen during the background read. Do not combine the
    # old current marker with a new generation's approvals. The next refresh retries.
    if not damaged_current and (live.current(environ) != n
            or any(found is not None and found.n != n for found in installed.values())
            or any(row['state'] == 'current' and row['n'] != n for row in rows)):
        return Facts()
    return Facts(n, rows, installed, proofs, identities)


def current(environ=os.environ):
    seen = facts(environ)
    return live.current(environ) if seen is None else seen.current


def generations(environ=os.environ):
    seen = facts(environ)
    return live.generations(environ) if seen is None else seen.generations


def receipt(n, environ=os.environ):
    seen = facts(environ)
    if seen is None:
        return live.receipt(n, environ)
    return next((row.get('receipt') for row in seen.generations if row['n'] == n), None)


def in_use(m, environ=os.environ):
    seen = facts(environ)
    if seen is None or m is None or m.namespace:
        return fiji_runner.in_use(m, environ)
    return seen.installed.get(repr(m))


def proven(binding, environ=os.environ):
    seen = facts(environ)
    if seen is None or '/' in binding:
        return live.proven(binding, environ)
    from gpu_worker import manifests as catalog
    m = catalog.get(binding)
    if m is not None and seen.identities.get(binding) != repr(m):
        return 'Its Fiji proof is being checked; check requirements again shortly.'
    return seen.proofs.get(binding, 'Its Fiji proof is being checked; check requirements again shortly.')

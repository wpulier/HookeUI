"""Private execution facts, independent of the authoritative conversation journal.

The live path only queues bounded immutable snapshots. One writer appends events
and updates a disposable SQLite index; inspection/rebuild run off the live loop.
No model reasoning, images, request transport packets or grants enter this store.
"""
from __future__ import annotations

from datetime import datetime, timezone
import base64
import json
import math
import os
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import uuid


class ExecutionArchive:
    MAX_EVENT = 128 * 1024

    def __init__(self, root: Path, *, capacity=256, retention_days=7, max_bytes=2 * 1024 ** 3):
        self.root = Path(root)
        self.events = self.root / 'events'
        self.index = self.root / 'index.sqlite3'
        self.clock_domain = str(uuid.uuid4())
        self.sequence = 0
        self.dropped = 0
        self.errors = 0
        self.gaps = 0
        self.last_error = None
        self.retention_days = retention_days
        self.max_bytes = max_bytes
        self.pruned = 0
        self.bytes = 0
        self._closed = False
        self._lock = threading.Lock()
        self._storage_lock = threading.Lock()
        self._queue = queue.Queue(maxsize=capacity)
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._work, name='execution-archive', daemon=True)
        self._thread.start()

    @staticmethod
    def valid_id(value):
        return isinstance(value, str) and re.fullmatch(r'e[a-zA-Z0-9-]{1,100}', value) is not None

    def offer(self, snapshot):
        """Never wait for storage or queue space. Caller owns the allowlisted facts."""
        try:
            if not self.valid_id(snapshot['root']['id']):
                raise ValueError('invalid execution id')
            with self._lock:
                if self._closed:
                    return False
                self.sequence += 1
                at = time.time()
                event = {'schema': 1, 'kind': 'execution-snapshot',
                         'at': datetime.fromtimestamp(at, timezone.utc).isoformat(timespec='milliseconds'),
                         'epochMs': round(at * 1000), 'monoNs': time.monotonic_ns(),
                         'clockDomain': self.clock_domain, 'sequence': self.sequence,
                         'snapshot': snapshot}
                data = json.dumps(event, allow_nan=False, separators=(',', ':')).encode() + b'\n'
                if len(data) > self.MAX_EVENT:
                    raise ValueError('execution event exceeds bound')
                self._queue.put_nowait((snapshot['root']['id'], data))
                return True
        except (ValueError, TypeError, KeyError, queue.Full):
            self.dropped += 1
            return False

    def status(self):
        return {'ready': self._ready.is_set(), 'dropped': self.dropped, 'writeErrors': self.errors,
                'recoveryGaps': self.gaps, 'lastError': self.last_error,
                'pending': self._queue.qsize(), 'clockDomain': self.clock_domain,
                'retentionDays': self.retention_days, 'maxUnmarkedBytes': self.max_bytes,
                'storedBytes': self.bytes, 'prunedTraces': self.pruned}

    def _upsert(self, db, event):
        snapshot = event['snapshot']; root = snapshot['root']
        snapshot['clockDomain'] = event['clockDomain']
        db.execute('INSERT INTO executions(id, at, updated, thread, closed, snapshot) VALUES(?,?,?,?,?,?) '
                   'ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, thread=excluded.thread, '
                   'closed=excluded.closed, snapshot=excluded.snapshot',
                   (root['id'], root['at'], event['epochMs'] / 1000, root.get('thread', ''),
                    bool(snapshot.get('closed')
                         or event['clockDomain'] != self.clock_domain), json.dumps(snapshot)))

    def reveal(self, trial_id):
        """Votes outlive the UI cache; queue this small event without disk I/O."""
        try:
            self._queue.put_nowait(('@reveal', str(trial_id)[:32]))
        except queue.Full:
            self.dropped += 1

    def _markers(self):
        result = []
        for path in (self.root / 'markers').glob('*.json'):
            try:
                result.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                # Unknown protection state must prevent pruning, not lose evidence.
                raise ValueError('unreadable protection marker')
        return result

    def _prune(self, db):
        with self._storage_lock:
            self._prune_locked(db)

    def _prune_locked(self, db):
        """Only completed, unmarked execution files. Never other data-home stores."""
        markers = self._markers()
        candidates = []; unmarked_size = 0; total = 0
        for rid, at, updated, closed in db.execute('SELECT id, at, updated, closed FROM executions ORDER BY updated, id'):
            path = self.events / (rid + '.jsonl')
            size = path.stat().st_size
            total += size
            protected = any(m['requestId'] == rid or at <= m['windowEnd'] and updated >= m['windowStart'] for m in markers)
            if not protected:
                unmarked_size += size
                if closed:
                    candidates.append((rid, path, updated, size))
        cutoff = time.time() - self.retention_days * 86400
        for rid, path, updated, size in candidates:
            if updated >= cutoff and unmarked_size <= self.max_bytes:
                continue
            # The tombstone remains outside the removable event file, so later
            # inspection can explain that evidence was deliberately expired.
            with (self.root / 'retention.jsonl').open('a') as journal:
                os.fchmod(journal.fileno(), 0o600)
                journal.write(json.dumps({'id': rid, 'at': time.time(), 'reason': 'unmarked retention'}) + '\n')
            path.unlink()
            db.execute('DELETE FROM executions WHERE id=?', (rid,))
            self.pruned += 1; total -= size; unmarked_size -= size
        self.bytes = total
        db.commit()

    def _work(self):
        db = None
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.root.chmod(0o700)
            self.events.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.events.chmod(0o700)
            # Tighten earlier preview files too; the derived index contains the
            # same private words as the event archive, including SQLite sidecars.
            for path in self.root.rglob('*'):
                if not path.is_symlink():
                    path.chmod(0o700 if path.is_dir() else 0o600)
            # The index is derived. Rebuild from append-only records after every
            # restart so a crash between append and index commit loses no facts.
            try:
                db = sqlite3.connect(self.index)
                self.index.chmod(0o600)
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('DROP TABLE IF EXISTS executions')
            except sqlite3.DatabaseError:
                if db is not None:
                    db.close()
                self.index.rename(self.root / ('index-corrupt-' + self.clock_domain + '.sqlite3'))
                db = sqlite3.connect(self.index)
                self.index.chmod(0o600)
            db.execute('CREATE TABLE executions(ordinal INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE, at REAL, updated REAL, thread TEXT, closed INTEGER, snapshot TEXT)')
            db.execute('DROP TABLE IF EXISTS revealed_trials')
            db.execute('CREATE TABLE revealed_trials(domain TEXT, id TEXT, PRIMARY KEY(domain,id))')
            reveals = self.root / 'reveals.jsonl'
            if reveals.exists():
                for line in reveals.read_text().splitlines():
                    try:
                        vote = json.loads(line)
                        db.execute('INSERT OR IGNORE INTO revealed_trials VALUES(?,?)', (vote['clockDomain'], vote['trialId']))
                    except (ValueError, KeyError):
                        self.gaps += 1
            for path in self.events.glob('e*.jsonl'):
                with path.open('rb') as stream:
                    for line in stream:
                        try:
                            event = json.loads(line)
                            if not line.endswith(b'\n') or event.get('schema') != 1:
                                raise ValueError('torn or unknown event')
                            self._upsert(db, event)
                        except (ValueError, KeyError, TypeError):
                            self.gaps += 1
            db.commit()
            self._prune(db)
        except Exception as exc:
            self.errors += 1
            self.last_error = type(exc).__name__
        finally:
            self._ready.set()
        try:
            while True:
                item = self._queue.get()
                try:
                    if item is None:
                        return
                    rid, data = item
                    if rid == '@reveal':
                        with (self.root / 'reveals.jsonl').open('a') as stream:
                            os.fchmod(stream.fileno(), 0o600)
                            stream.write(json.dumps({'schema': 1, 'trialId': data, 'clockDomain': self.clock_domain, 'at': time.time()}) + '\n')
                        if db is not None:
                            db.execute('INSERT OR IGNORE INTO revealed_trials VALUES(?,?)', (self.clock_domain, data))
                            db.commit()
                        continue
                    with (self.events / (rid + '.jsonl')).open('ab') as stream:
                        os.fchmod(stream.fileno(), 0o600)
                        stream.write(data)
                        stream.flush()
                    if db is not None:
                        self._upsert(db, json.loads(data))
                        db.commit()
                        if self._queue.empty():
                            self._prune(db)
                except Exception as exc:
                    self.errors += 1
                    self.last_error = type(exc).__name__
                finally:
                    self._queue.task_done()
        finally:
            if db is not None:
                db.close()

    def inspect(self, rid):
        if not self.valid_id(rid) or not self._ready.wait(5):
            return None
        try:
            with sqlite3.connect(f'file:{self.index}?mode=ro', uri=True) as db:
                row = db.execute('SELECT snapshot FROM executions WHERE id=?', (rid,)).fetchone()
                revealed = set(db.execute('SELECT domain,id FROM revealed_trials'))
            if row:
                result = json.loads(row[0])
                if (result.get('clockDomain'), result['root'].get('trialId')) in revealed:
                    result['status'] = 'available'
                result['durable'] = True
                result['archive'] = self.status()
                # No automatic assertion that a process interrupted mid-request
                # is still doing work, nor a fabricated failure/completion.
                if not result.get('closed') and result.get('clockDomain') != self.clock_domain:
                    result['interrupted'] = True
                return result
        except (sqlite3.Error, ValueError):
            return None

    def references(self, thread=None, *, limit=256):
        if not self._ready.wait(5):
            return []

        try:
            with sqlite3.connect(f'file:{self.index}?mode=ro', uri=True) as db:
                query = 'SELECT snapshot FROM executions'
                values = []
                if thread is not None:
                    query += ' WHERE thread=?'; values.append(thread)
                rows = db.execute(query + ' ORDER BY at DESC, id DESC LIMIT ?', [*values, min(256, max(1, limit))]).fetchall()
            return [json.loads(row[0])['root'] for row in rows]
        except (sqlite3.Error, ValueError):
            return []

    def usage(self, *, limit=500):
        """Bounded observed tool use, off-loop. Never label absent history as unused."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('usage limit is 1–1000')
        if not self._ready.wait(5):
            raise OSError('execution archive is still opening')
        with sqlite3.connect(f'file:{self.index}?mode=ro', uri=True) as db:
            rows = db.execute('SELECT at,snapshot FROM executions ORDER BY at DESC,id DESC LIMIT ?', (limit + 1,)).fetchall()
        counts, dates = {}, []
        for at, body in rows[:limit]:
            snap = json.loads(body)
            if snap.get('status') == 'blind':
                continue  # unrevealed alternatives cannot leak through usage counts
            dates.append(at)
            for step in snap.get('steps', []):
                operation = step.get('operation', '')
                if not operation.startswith('tool:') or step.get('outcome') == 'running':
                    continue
                name = operation[5:]
                row = counts.setdefault(name, {'count': 0, 'failed': 0, 'last': at})
                row['count'] += 1
                row['failed'] += step.get('outcome') in ('failed', 'cancelled')
                row['last'] = max(row['last'], at)
        return {'tools': counts, 'from': min(dates) if dates else None, 'through': max(dates) if dates else None,
                'traces': len(dates), 'limited': len(rows) > limit, 'complete': False,
                'reason': 'Recorded tool executions only. Detailed logging, retention and unavailable history leave gaps; command variants and unrecorded/background use are unknown.'}

    def search(self, query='', *, cursor=None, limit=30):
        """Explicit off-loop history inspection; keyset pages never join chat turns.

        The cursor pins the index generation and insertion ceiling. New requests
        cannot displace later pages, even if the wall clock steps backwards.
        """
        if not isinstance(query, str) or len(query) > 200 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('query is at most 200 characters; page size is 1–100')
        if not self._ready.wait(5):
            raise OSError('execution archive is still opening')
        with sqlite3.connect(f'file:{self.index}?mode=ro', uri=True) as db:
            ceiling = db.execute('SELECT COALESCE(MAX(rowid),0) FROM executions').fetchone()[0]
            last = None
            if cursor:
                try:
                    if not isinstance(cursor, str) or len(cursor) > 4096:
                        raise ValueError('invalid search cursor')
                    state = json.loads(base64.urlsafe_b64decode(cursor))
                    if state['domain'] != self.clock_domain or state['query'] != query:
                        raise ValueError('history changed; restart the search')
                    ceiling, last = state['ceiling'], state['last']
                    if (type(ceiling) is not int or not 0 <= ceiling <= 2**63 - 1
                            or not isinstance(last, list) or len(last) != 2
                            or type(last[0]) not in (int, float) or not math.isfinite(last[0])
                            or not isinstance(last[1], str) or not self.valid_id(last[1])):
                        raise ValueError('invalid search cursor')
                except (KeyError, TypeError, ValueError, OverflowError) as exc:
                    raise ValueError('history changed or cursor is invalid; restart the search') from exc
            where = ['rowid <= ?']; args = [ceiling]
            if query:
                where.append("instr(lower(COALESCE(json_extract(snapshot, '$.context.words'), '') || ' ' || id || ' ' || thread), lower(?)) > 0")
                args.append(query)
            if last:
                where.append('(at < ? OR (at = ? AND id < ?))'); args.extend([last[0], last[0], last[1]])
            rows = db.execute('SELECT at,id,snapshot FROM executions WHERE ' + ' AND '.join(where)
                              + ' ORDER BY at DESC,id DESC LIMIT ?', [*args, limit + 1]).fetchall()
        entries = []
        for at, rid, body in rows[:limit]:
            snapshot = json.loads(body)
            entries.append({**snapshot['root'], 'words': snapshot.get('context', {}).get('words', ''),
                            'owner': snapshot.get('context', {}).get('owner', {}),
                            'interrupted': not snapshot.get('closed') and snapshot.get('clockDomain') != self.clock_domain})
        next_cursor = None
        if len(rows) > limit:
            at, rid, _ = rows[limit - 1]
            next_cursor = base64.urlsafe_b64encode(json.dumps({'domain': self.clock_domain, 'query': query,
                'ceiling': ceiling, 'last': [at, rid]}).encode()).decode()
        return {'entries': entries, 'nextCursor': next_cursor, 'archive': self.status()}

    def markers(self, rid):
        """Local inspection, off the request path; markers remain authoritative on disk."""
        return sorted((m for m in self._markers() if m.get('requestId') == rid), key=lambda m: m['at'])

    def mark(self, rid, marker_id, note=''):
        """Explicit local preservation; never starts any recording channel."""
        if not self.valid_id(rid) or not re.fullmatch(r'[a-zA-Z0-9-]{1,100}', marker_id):
            raise ValueError('invalid marker identity')
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError('a marker note is at most 2000 characters')
        self.flush()
        if self.inspect(rid) is None:
            raise ValueError('execution is unavailable; it cannot be reconstructed')
        directory = self.root / 'markers'; directory.mkdir(exist_ok=True, mode=0o700)
        path = directory / (marker_id + '.json')
        # Retention and markers serialize on the same lock; a marker cannot be
        # acknowledged while its selected evidence is being pruned.
        with self._storage_lock:
            if self.inspect(rid) is None:
                raise ValueError("execution expired before the marker was saved")
            if path.exists():
                previous = json.loads(path.read_text())
                if previous['requestId'] != rid or previous['note'] != note:
                    raise ValueError('marker identity already belongs to another request or note')
                return previous
            at = time.time()
            marker = {'schema': 1, 'id': marker_id, 'requestId': rid, 'note': note,
                      'at': at, 'windowStart': at - 60, 'windowEnd': at + 30,
                      'recordingStarted': False, 'recordings': [],
                      'missing': ['Recording links are not yet correlated; this marker contains execution evidence only.']}
            temporary = directory / (marker_id + '.tmp')
            with temporary.open('w') as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(marker, stream); stream.flush(); os.fsync(stream.fileno())
            temporary.replace(path)
            return marker

    def flush(self):
        """Off-loop lifecycle/test barrier; never called by an action."""
        self._queue.join()

    def close(self):
        with self._lock:
            self._closed = True
        self._queue.put(None)
        self._thread.join(timeout=10)

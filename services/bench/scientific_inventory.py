"""Reproducible upstream coverage inventory; never imports or executes upstream code.

The extraction is deliberately conservative: source declarations are review candidates,
not proof of parity or an assertion that every declaration is a public interaction.
Review dispositions must account for each candidate, including explicit exclusions.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile

REVISION = 'ebfc4a0a1e5386f41582761204f416fffe6cd0b6'
ARCHIVE_SHA256 = '3a29672c608ee6a8e84a488d7c5c122915f5c852757d401736f668bf60c740a6'
PREFIX = 'napari-main/'
MAX_ARCHIVE = 32 << 20
MAX_FILE = 4 << 20
MAX_SOURCE = 128 << 20


class InvalidInventory(ValueError):
    pass


def sha(body):
    return hashlib.sha256(body).hexdigest()


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name(node.value)
        return f'{base}.{node.attr}' if base else node.attr
    return ''


def declarations(source):
    """Include private helpers too: widgets and registered callbacks often use them."""
    tree = ast.parse(source)
    rows = []
    def emit(node, symbol, kind):
        rows.append({'symbol': symbol, 'kind': kind, 'line': node.lineno,
                     'end_line': node.end_lineno})
    def visit(body, scope=''):
        for node in body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol = f'{scope}.{node.name}' if scope else node.name
                kind = 'class' if isinstance(node, ast.ClassDef) else 'callable'
                if kind == 'callable':
                    decorators = [_name(d.func if isinstance(d, ast.Call) else d) for d in node.decorator_list]
                    if 'property' in decorators: kind = 'property'
                    elif any(d.endswith('.setter') for d in decorators): kind = 'setter'
                    elif any('register' in d or 'bind_key' in d for d in decorators): kind = 'registered-callback'
                emit(node, symbol, kind)
                visit(node.body, symbol)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                emit(node, f'{scope}.{node.target.id}' if scope else node.target.id, 'field')
            # Conditional definitions must not disappear from platform inventories.
            elif isinstance(node, (ast.If, ast.With, ast.Try, ast.For)):
                visit(node.body, scope)
                visit(getattr(node, 'orelse', []), scope)
                visit(getattr(node, 'finalbody', []), scope)
                for handler in getattr(node, 'handlers', []): visit(handler.body, scope)
    visit(tree.body)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call): continue
        name = _name(node.func)
        if name.endswith(('EmitterGroup', 'Signal')):
            for keyword in node.keywords:
                if keyword.arg: emit(node, f'{name}.{keyword.arg}', 'event')
        if any(word in name for word in ('Action', 'register_action', 'bind_key')):
            emit(node, name, 'registration')
    return sorted(rows, key=lambda row: (row['line'], row['kind'], row['symbol']))


def extract(path):
    path = Path(path)
    if path.stat().st_size > MAX_ARCHIVE:
        raise InvalidInventory('Reference archive exceeds its bounded source allowance')
    if sha(path.read_bytes()) != ARCHIVE_SHA256:
        raise InvalidInventory('Reference archive differs from the pinned supplied source')
    sources, items = {}, []
    total = 0
    with zipfile.ZipFile(path) as archive:
        names = set()
        for member in sorted(archive.infolist(), key=lambda item: item.filename):
            name = member.filename
            parts = PurePosixPath(name).parts
            if (name in names or not name.startswith(PREFIX) or '..' in parts or '\\' in name
                    or stat.S_ISLNK(member.external_attr >> 16)):
                raise InvalidInventory('Reference archive has ambiguous or unsafe members')
            names.add(name)
            if member.is_dir(): continue
            relative = name[len(PREFIX):]
            # Include source, examples and tests; no current Hooke feature list filters this census.
            if not relative.endswith('.py'): continue
            total += member.file_size
            if member.file_size > MAX_FILE or total > MAX_SOURCE:
                raise InvalidInventory('Reference source exceeds its bounded inspection allowance')
            body = archive.read(member)
            sources[relative] = {'sha256': sha(body), 'bytes': len(body)}
            for ordinal, row in enumerate(declarations(body)):
                identity = sha(f'{relative}:{row["line"]}:{row["kind"]}:{row["symbol"]}:{ordinal}'.encode())[:24]
                items.append({'id': f'napari.{identity}', 'source': relative, **row})
    return {'format': 1, 'revision': REVISION, 'archive_sha256': ARCHIVE_SHA256,
            'class': 'source_inspection', 'qualification_eligible': False,
            'sources': sources, 'items': items}


def validate_review(inventory, review, groups):
    """No omitted candidate or empty child can disappear behind a group pass."""
    if type(review) is not dict or review.get('format') != 1:
        raise InvalidInventory('Unsupported coverage review format')
    identity = sha(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode())
    if review.get('inventory_sha256') != identity:
        raise InvalidInventory('Coverage review does not identify this exact source census')
    records = review.get('items')
    if type(records) is not list or any(type(row) is not dict for row in records):
        raise InvalidInventory('Coverage review needs individual source dispositions')
    ids = [row.get('id') for row in records]
    expected = {row['id'] for row in inventory['items']}
    if any(type(i) is not str for i in ids) or len(set(ids)) != len(ids) or set(ids) != expected:
        raise InvalidInventory('Coverage review has missing, extra or duplicate source items')
    children = set()
    for row in records:
        if row.get('disposition') == 'excluded':
            if not isinstance(row.get('reason'), str) or not row['reason'].strip():
                raise InvalidInventory('Every excluded item needs its upstream scope reason')
            continue
        if row.get('disposition') != 'required' or row.get('group') not in groups:
            raise InvalidInventory('Every required item needs an existing acceptance group')
        for field in ('owner', 'behavior', 'hooke_interface', 'upstream_limits'):
            if type(row.get(field)) is not str or not row[field].strip():
                raise InvalidInventory(f'Required coverage is missing {field}')
        cases = row.get('cases')
        if type(cases) is not list or not cases:
            raise InvalidInventory('Required coverage needs atomic executable case identities')
        for case in cases:
            if type(case) is not dict or type(case.get('id')) is not str or not case['id'] or case['id'] in children:
                raise InvalidInventory('Atomic case identities must be unique and nonempty')
            children.add(case['id'])
            for field in ('procedure', 'oracle', 'criteria', 'entrypoint'):
                if type(case.get(field)) is not str or not case[field].strip():
                    raise InvalidInventory(f'Atomic coverage is missing {field}')
    return {'source_items': len(records), 'atomic_cases': len(children),
            'qualification_eligible': False,
            'independent_review': 'required'}


# Atomic acceptance cases (H00). Rules in INVENTORY/rules.json select public items from the
# pinned source; every selected item is routed to one acceptance group or excluded with a
# stated reason, and a selector that finds nothing is an error, so no item drops silently.
# Hand-written children (INVENTORY/manual.json) cite source symbols that must exist. The
# generated INVENTORY/atomic-cases.json is what the acceptance runner reads.
INVENTORY = Path(__file__).resolve().parents[2] / 'services/bench/specification'
EXTERNAL_CLASSES = ('clean_os_installation', 'physical_device', 'independent_contributor',
                    'fresh_researcher_comparison')
EVIDENCE_CLASSES = ('source_inspection', 'synthetic_fixture', 'actual_upstream_worker',
                    'isolated_profile_browser', *EXTERNAL_CLASSES)
KINDS = ('field', 'property', 'method', 'function', 'class', 'enum-value', 'action', 'menu-command',
         'shortcut', 'event', 'control', 'setting', 'contribution', 'procedure', 'hooke')


def _archive_files(path, suffixes=('.py', '.yaml', '.yml')):
    """The pinned archive's text sources, after the same identity and safety checks as extract()."""
    path = Path(path)
    if path.stat().st_size > MAX_ARCHIVE or sha(path.read_bytes()) != ARCHIVE_SHA256:
        raise InvalidInventory('Reference archive differs from the pinned supplied source')
    files = {}
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            name = member.filename
            if (not name.startswith(PREFIX) or '..' in PurePosixPath(name).parts or '\\' in name
                    or stat.S_ISLNK(member.external_attr >> 16)):
                raise InvalidInventory('Reference archive has ambiguous or unsafe members')
            if member.is_dir() or not name.endswith(suffixes) or member.file_size > MAX_FILE:
                continue
            files[name[len(PREFIX):]] = archive.read(member)
    return files


def slug(text):
    out = ''.join(ch.lower() if ch.isalnum() else '-' for ch in text)
    while '--' in out:
        out = out.replace('--', '-')
    return out.strip('-')


def _decorators(node):
    return [_name(d.func if isinstance(d, ast.Call) else d) for d in node.decorator_list]


def _class(tree, name, path):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise InvalidInventory(f'{path}: class {name} is not in the pinned source')


def _literal(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return ast.unparse(node)
    if isinstance(node, ast.Call) and _name(node.func) == 'auto':
        return 'auto()'
    return ast.unparse(node) if node is not None else None


def _loop_values(tree, target):
    """Values of a literal `for NAME in (...)` loop around a templated registration."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and node.target.id == target and isinstance(node.iter, (ast.Tuple, ast.List))):
            values = [_literal(item) for item in node.iter.elts]
            if all(isinstance(v, str) for v in values):
                return values
    return None


def _fstring(node, tree):
    """An f-string id is expanded over its literal loop, or it cannot be inventoried."""
    if isinstance(node, ast.Constant):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        names = [v.value.id for v in node.values if isinstance(v, ast.FormattedValue)
                 and isinstance(v.value, ast.Name)]
        if len(names) == 1 and (values := _loop_values(tree, names[0])):
            out = []
            for value in values:
                out.append(''.join(part.value if isinstance(part, ast.Constant) else value
                                   for part in node.values))
            return out
    raise InvalidInventory(f'Templated registration {ast.unparse(node)} has no literal expansion')


def select(tree, rule, path):
    """Items one selector finds in one source: (symbol, line, detail)."""
    kind = rule['select']
    out = []
    if kind in ('fields', 'properties', 'methods', 'enum', 'events', 'evented-fields'):
        node = _class(tree, rule['class'], path)
        cls = node.name
        setters = {b.name for b in node.body if isinstance(b, ast.FunctionDef)
                   and any(d.endswith('.setter') for d in _decorators(b))}
        for body in node.body:
            if kind in ('fields', 'evented-fields') and isinstance(body, ast.AnnAssign) \
                    and isinstance(body.target, ast.Name) and not body.target.id.startswith('_') \
                    and 'ClassVar' not in ast.unparse(body.annotation):
                name = body.target.id
                symbol = f'{cls}.events.{name}' if kind == 'evented-fields' else f'{cls}.{name}'
                out.append((symbol, body.lineno, {'default': _literal(body.value)}))
            elif kind == 'properties' and isinstance(body, ast.FunctionDef) and not body.name.startswith('_'):
                decorators = _decorators(body)
                if 'property' in decorators or any(d.endswith('.getter') for d in decorators):
                    out.append((f'{cls}.{body.name}', body.lineno, {'settable': body.name in setters}))
            elif kind == 'methods' and isinstance(body, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and not body.name.startswith('_') and not any(o[0] == f'{cls}.{body.name}' for o in out):
                decorators = _decorators(body)
                if not ('property' in decorators or any(d.endswith(('.setter', '.getter', '.deleter'))
                                                         for d in decorators)):
                    out.append((f'{cls}.{body.name}', body.lineno, {}))
            elif kind == 'enum' and isinstance(body, ast.Assign):
                for target in body.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        value = _literal(body.value)
                        if value == 'auto()':
                            value = target.id.lower()
                        out.append((f'{cls}.{target.id}', body.lineno, {'value': value}))
        if kind == 'events':
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and _name(call.func).endswith(('EmitterGroup', 'events.add')):
                    for keyword in call.keywords:
                        if keyword.arg and keyword.arg not in ('source', 'auto_connect') \
                                and not keyword.arg.startswith('_'):
                            out.append((f'{cls}.events.{keyword.arg}', call.lineno, {}))
    elif kind == 'public':
        # Every public class member and function of a public API module; kinds follow the member.
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith('_') \
                    and not any(o[0] == node.name for o in out):  # typing overloads repeat one function
                out.append((node.name, node.lineno, {'kind': 'function'}))
            elif isinstance(node, ast.ClassDef) and not node.name.startswith('_'):
                members = []
                for sub in ('fields', 'properties', 'methods', 'enum'):
                    rows = select(tree, {'select': sub, 'class': node.name}, path)
                    members += [(sym, line, {**d, 'kind': {'fields': 'field', 'properties': 'property',
                                                         'methods': 'method', 'enum': 'enum-value'}[sub]})
                                for sym, line, d in rows if not any(sym == m[0] for m in members)]
                out += members or [(node.name, node.lineno, {'kind': 'class'})]
    elif kind == 'classes':
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and not node.name.startswith('_'):
                out.append((node.name, node.lineno, {}))
    elif kind == 'functions':
        seen = set()  # typing overloads repeat one public function
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith('_') \
                    and node.name not in seen:
                seen.add(node.name)
                out.append((node.name, node.lineno, {}))
    elif kind == 'registered':
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                name = _name(decorator.func)
                if 'register' in name or 'bind_key' in name:
                    text = next((a.value for a in decorator.args if isinstance(a, ast.Constant)
                                 and isinstance(a.value, str)), None)
                    keys = ast.unparse(decorator.args[0]) if 'bind_key' in name and decorator.args else None
                    out.append((node.name, node.lineno, {'title': text, 'keys': keys}))
                    break
    elif kind == 'actions':
        for call in ast.walk(tree):
            if isinstance(call, ast.Call) and _name(call.func).endswith('Action'):
                keywords = {k.arg: k.value for k in call.keywords}
                if 'id' not in keywords or isinstance(keywords['id'], ast.Name):
                    continue
                titles = _fstring(keywords['title'], tree) if 'title' in keywords else [None]
                callback = keywords.get('callback')
                if isinstance(callback, ast.Call):  # partial(function, ...) or a factory call
                    callback = callback.args[0] if _name(callback.func).endswith('partial') and callback.args \
                        else callback.func
                callback = _name(callback) if callback is not None else ''
                for ident, title in zip(_fstring(keywords['id'], tree), titles * 99):
                    out.append((ident, call.lineno, {'title': title, 'callback': callback or None}))
    elif kind == 'tuples':
        # Rows such as (command id, title, attribute path) in a named literal table.
        for node in tree.body:
            target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, 'target', None)
            if isinstance(target, ast.Name) and target.id == rule['name']:
                for item in ast.walk(node.value):
                    if isinstance(item, ast.Tuple) and len(item.elts) >= 2 and all(
                            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in item.elts):
                        out.append((item.elts[0].value, item.lineno,
                                    {'title': item.elts[1].value, 'path': [e.value for e in item.elts[2:]]}))
    elif kind == 'newtypes':
        # Annotation types that select input controls and output handling (napari.types).
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                    and _name(node.value.func) == 'NewType' and isinstance(node.targets[0], ast.Name):
                out.append((node.targets[0].id, node.lineno, {}))
    elif kind == 'dict-keys':
        for node in tree.body:
            target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, 'target', None)
            if isinstance(target, ast.Name) and target.id == rule['name'] and isinstance(node.value, ast.Dict):
                for key, value in zip(node.value.keys, node.value.values):
                    if isinstance(key, ast.Constant):
                        out.append((key.value, key.lineno, {'keys': ast.unparse(value)}))
    else:
        raise InvalidInventory(f'Unknown selector {kind}')
    return out


def _yaml_items(body, rule):
    import yaml
    document = yaml.safe_load(body)
    out = []
    for section in rule['sections']:
        for entry in document.get('contributions', {}).get(section, []):
            ident = entry.get('id') or entry.get('command')
            detail = {k: v for k, v in entry.items() if k in ('title', 'display_name', 'filename_patterns',
                                                                'layer_types', 'key', 'python_name')}
            qualifier = entry.get('key') or ':'.join(filter(None, [','.join(entry.get('layer_types', [])),
                                                                    entry.get('display_name')]))
            out.append((f'{section}:{ident}:{qualifier}' if qualifier else f'{section}:{ident}', 0, detail))
    return out


REQUIREMENT = {
    'field': 'Read and change `{symbol}` with its pinned-upstream meaning, through an ordinary Hooke control or its API equivalent.',
    'property': 'Expose `{symbol}` with its pinned-upstream value and meaning{settable}.',
    'method': 'Provide the behaviour of `{symbol}` with its pinned-upstream result.',
    'class': 'Provide the behaviour of `{symbol}` with its pinned-upstream result.',
    'function': 'Provide the behaviour of `{symbol}` with its pinned-upstream result.',
    'enum-value': 'Offer `{symbol}` (`{value}`) as a choice with its pinned-upstream effect.',
    'action': 'Offer the action "{title}" (`{symbol}`) with its pinned-upstream effect.',
    'menu-command': 'Offer the command "{title}" (`{symbol}`) with its pinned-upstream effect.',
    'shortcut': 'Deliver the default shortcut `{keys}` to `{symbol}` through real keyboard routing, without conflicts.',
    'event': 'Emit `{symbol}` for every change it reports upstream, with the same payload meaning, to registered callbacks.',
    'control': 'Provide the control `{symbol}`, with every option and state it offers upstream, as an ordinary Hooke control.',
    'setting': 'Honour the setting `{symbol}` with its pinned-upstream default and effect.',
    'contribution': 'Support the builtin contribution `{symbol}` ({detail}) with its pinned-upstream behaviour.',
}


def _requirement(kind, symbol, detail):
    text = REQUIREMENT[kind]
    settable = '; changing it has the upstream effect' if detail.get('settable') else ' (read-only upstream)'
    return text.format(symbol=symbol, settable=settable if kind == 'property' else '',
                       value=detail.get('value'), title=detail.get('title') or symbol,
                       keys=detail.get('keys'), detail=json.dumps(detail, sort_keys=True, default=str))


def _groups():
    """The 71 handoff groups and nine review regressions with their owning work packages."""
    import re
    text = (Path(__file__).resolve().parents[2] / 'docs/testing/scientific-acceptance.md').read_text()
    groups = {}
    for ident, title, owner in re.findall(r'^\| `((?:P[1-8]|Q|W)\.[^`]+)` \| ([^|]+) \| (H\d\d) \|$', text, re.M):
        groups[ident] = {'title': title.strip(), 'owner': owner}
    for ident, owner, title in re.findall(r'^\| (R\d\d\.[a-z0-9-]+) \| ([^|]+) \| ([^|]+) \|$', text, re.M):
        groups[ident] = {'title': title.strip(), 'owner': owner.strip()}
    if len(groups) != 80:
        raise InvalidInventory(f'Expected 71 groups and 9 regressions in acceptance.md, found {len(groups)}')
    return groups


def _route(rule, symbol):
    import re
    leaf = symbol.rsplit('.', 1)[-1]
    for pattern, target, *reason in rule.get('routes', []):
        if re.search(pattern, symbol) or re.search(pattern, leaf):
            return target, (reason[0] if reason else None)
    if 'group' in rule:
        return rule['group'], None
    raise InvalidInventory(f'{rule["source"]}: {symbol} is not routed to a group or excluded')


def atomic_cases(archive, rules=None, manual=None):
    """Generate every atomic child; deterministic for the pinned archive, rules and manual cases."""
    import fnmatch
    rules = rules if rules is not None else json.loads((INVENTORY / 'rules.json').read_text())
    manual = manual if manual is not None else json.loads((INVENTORY / 'manual.json').read_text())
    files = _archive_files(archive)
    groups = _groups()
    trees = {}
    def tree(path):
        if path not in trees:
            trees[path] = ast.parse(files[path])
        return trees[path]
    children, excluded, cites, used_sources, symbols, route_excluded = [], [], set(), set(), {}, set()
    defaults = rules['evidence_defaults']
    def evidence(group, rule):
        family = group.split('.')[0]
        return (rule or {}).get('evidence') or rules['group_evidence'].get(group) or defaults[family]
    def add(child):
        # An absent procedure, oracle or criteria means null: not ready, never passed. (hooke_equivalent: a harness
        # must state it; a hand-written child records it where its package writes it. README.)
        limits = child.get('upstream_limits', rules['limits'].get(child['group'], []))
        child.pop('upstream_limits', None)
        if limits:
            child['upstream_limits'] = limits
        if not child.get('detail'):
            child.pop('detail', None)
        if child.get('source') is None:
            child.pop('source', None)
        children.append(child)
    for index, rule in enumerate(rules['selectors']):
        pattern = rule['source'].split('/')
        paths = sorted(p for p in files if len(p.split('/')) == len(pattern)
                       and all(fnmatch.fnmatchcase(a, b) for a, b in zip(p.split('/'), pattern)))
        if not paths:
            raise InvalidInventory(f'Selector {index}: {rule["source"]} matches no pinned source')
        found = 0
        for path in paths:
            items = _yaml_items(files[path], rule) if rule['select'] == 'yaml' else select(tree(path), rule, path)
            for symbol, line, detail in items:
                found += 1
                target, reason = _route(rule, symbol)
                source = {'path': path, 'symbol': symbol, 'line': line}
                used_sources.add(path)
                if target == 'exclude':
                    # An excluded item is not cited: its census declaration stays unreviewed.
                    excluded.append({'source': source, 'reason': reason or rule.get('reason')})
                    route_excluded.add((path, symbol))
                    if not (reason or rule.get('reason')):
                        raise InvalidInventory(f'{symbol}: an exclusion needs its reason')
                    continue
                cites.add((path, symbol))
                cites.add((path, symbol.split('.')[0]))  # the class declaration itself, not its other members
                if detail.get('callback'):
                    cites.add((path, detail['callback'].rsplit('.', 1)[-1]))
                    cites.add((path, detail['callback']))
                if rule['select'] == 'events':
                    cites.add((path, f'EmitterGroup.{symbol.rsplit(".", 1)[-1]}'))
                if target not in groups:
                    raise InvalidInventory(f'{symbol}: routed to unknown group {target}')
                kind = detail.pop('kind', None) or rule['kind']
                stem = slug(PurePosixPath(path).stem) + '-' if rule.get('file_prefix') else ''
                add({'id': f'{target}.{rule.get("prefix", "")}{stem}{slug(symbol)}',
                     'group': target, 'owner': groups[target]['owner'], 'kind': kind,
                     'requirement': _requirement(kind, symbol, detail), 'evidence_class': evidence(target, rule),
                     'basis': 'pinned-source', 'source': source, 'detail': detail})
        if not found:
            raise InvalidInventory(f'Selector {index} ({rule["select"]} in {rule["source"]}) found nothing')
    for case in manual['children']:
        child = dict(case)
        if child['group'] not in groups:
            raise InvalidInventory(f'{child["id"]}: unknown group')
        child.setdefault('owner', groups[child['group']]['owner'])
        child.setdefault('evidence_class', evidence(child['group'], None))
        child.setdefault('detail', {})
        cited = []
        for path, symbol in child.pop('cites', []):
            if path in files and path not in symbols:
                symbols[path] = [row['symbol'] for row in declarations(files[path])]
            exact = [s for s in symbols.get(path, []) if s == symbol or s.endswith('.' + symbol)]
            if path not in files or not exact:
                raise InvalidInventory(f'{child["id"]}: cites {path}:{symbol}, absent from the pinned source')
            cited.append({'path': path, 'symbol': symbol})
            cites.update((path, s) for s in exact)
            used_sources.add(path)
        child['source'] = cited[0] if len(cited) == 1 else ({'all': cited} if cited else None)
        add(child)
    for group, meta in groups.items():
        family = group.split('.')[0]
        for template in rules['templates']:
            if family not in template['families'] or group in template.get('skip', []):
                continue
            add({'id': f'{group}.{template["suffix"]}', 'group': group, 'owner': meta['owner'],
                 'kind': 'hooke', 'requirement': template['requirement'].format(title=meta['title']),
                 'evidence_class': template.get('evidence') or evidence(group, None),
                 'basis': template['basis'], 'source': None, 'detail': {}})
    overrides = rules.get('child_evidence', {})
    for child in children:
        if child['id'] in overrides:
            child['evidence_class'] = overrides[child['id']]
    unknown = sorted(set(overrides) - {c['id'] for c in children})
    if unknown:
        raise InvalidInventory(f'Evidence overrides for absent children: {unknown}')
    # A harness template (bench/scientific_harnesses.py) gives every child it matches its procedure, oracle and criteria.
    for harness in rules.get('harnesses', []):
        # Only the children the harness has a behaviour recipe for, by symbol or by exact id: a symbol that merely
        # resolves proves nothing.
        if 'ids' in harness:
            wanted = set(harness['ids'])
            matched = [c for c in children if c['id'] in wanted and c['group'].split('.')[0] in harness['families']
                       and c['evidence_class'] == harness['evidence']]
            unmatched = wanted - {c['id'] for c in matched}
        else:
            symbols = set(harness['symbols'])
            matched = [c for c in children if c['group'].split('.')[0] in harness['families'] and c['kind'] in harness['kinds']
                       and c['evidence_class'] == harness['evidence'] and c['basis'] == harness['basis']
                       and c['source']['symbol'] in symbols]
            unmatched = symbols - {c['source']['symbol'] for c in matched}
        if not matched or unmatched:
            raise InvalidInventory(f'Harness {harness["name"]} lists symbols or ids with no child: {sorted(unmatched)[:5]}')
        if not harness.get('hooke_equivalent'):  # what in Hooke the harness exercises: never left null
            raise InvalidInventory(f'Harness {harness["name"]} states no hooke_equivalent')
        for child in matched:
            if 'harness' in child:
                raise InvalidInventory(f'{child["id"]}: matched by two harnesses')
            child.update(harness=harness['name'], procedure=harness['procedure'], oracle=harness['oracle'],
                         criteria=harness['criteria'], hooke_equivalent=harness['hooke_equivalent'])
    specify(children, manual.get('specifications', {}))
    children.sort(key=lambda c: c['id'])
    ids = [c['id'] for c in children]
    duplicates = sorted({i for i in ids if ids.count(i) > 1}) if len(set(ids)) != len(ids) else []
    if duplicates:
        raise InvalidInventory(f'Duplicate atomic ids: {duplicates[:10]}')
    for child in children:
        if child['kind'] not in KINDS or child['evidence_class'] not in EVIDENCE_CLASSES:
            raise InvalidInventory(f'{child["id"]}: unknown kind or evidence class')
    empty = sorted(g for g in groups if not any(c['group'] == g for c in children))
    if empty:
        raise InvalidInventory(f'Groups without atomic children: {empty}')
    return {'format': 1, 'revision': REVISION, 'archive_sha256': ARCHIVE_SHA256,
            'rules_sha256': sha(json.dumps(rules, sort_keys=True).encode()),
            'manual_sha256': sha(json.dumps(manual, sort_keys=True).encode()),
            'class': 'source_inspection', 'qualification_eligible': False,
            'sources': {p: sha(files[p]) for p in sorted(used_sources)},
            'groups': {g: {**m, 'children': ids_of(children, g)} for g, m in groups.items()},
            'census': census_coverage(archive, cites, rules['census_exclusions'], route_excluded),
            'unpinned_sources': rules.get('unpinned_sources', []),
            'excluded': sorted(excluded, key=lambda e: (e['source']['path'], e['source']['symbol'])),
            'children': children}


SPECIFIED = ('procedure', 'oracle', 'criteria', 'hooke_equivalent')


def specify(children, specifications):
    """A work package's procedure, oracle, criteria and Hooke equivalent for a child the selectors generated.

    Hand-written in manual.json `specifications`, one record per child. A specification can only fill those four
    fields: never the requirement, kind, owner or evidence class, and never a status or evidence, so specifying a
    child makes it ready to be checked, not passed.
    """
    by_id = {child['id']: child for child in children}
    if type(specifications) is dict:  # the shared format: child id -> its fields
        for ident, fields in specifications.items():
            if type(fields) is not dict or set(fields) - set(SPECIFIED):
                raise InvalidInventory(f'{ident}: a specification can only give {", ".join(SPECIFIED)}')
        specifications = [{'id': ident, **fields} for ident, fields in specifications.items()]
    seen = set()
    for spec in specifications:
        ident = spec.get('id') if type(spec) is dict else None
        if ident not in by_id:
            raise InvalidInventory(f'Specification for an absent child: {ident}')
        if ident in seen:
            raise InvalidInventory(f'{ident}: specified twice')
        seen.add(ident)
        fields = set(spec) - {'id'}
        if not fields or fields - set(SPECIFIED):
            raise InvalidInventory(f'{ident}: a specification can only give {", ".join(SPECIFIED)}')
        child = by_id[ident]
        for name in sorted(fields):
            if type(spec[name]) is not str or not spec[name].strip():
                raise InvalidInventory(f'{ident}: {name} must be text')
            if name in child:
                raise InvalidInventory(f'{ident}: {name} is already specified differently')
            child[name] = spec[name]
        if not {'procedure', 'oracle', 'criteria'} <= set(child):
            raise InvalidInventory(f'{ident}: a specification must leave a procedure, an oracle and criteria')


def ids_of(children, group):
    return sum(1 for c in children if c['group'] == group)


def census_coverage(archive, cites, exclusions, route_excluded=frozenset()):
    """Account for every census declaration: cited exactly by a child, excluded by a stated rule, or unreviewed.

    A citation covers only its own declaration (and the class that declares it), never a class's other
    members; an item a selector excluded stays unreviewed until the coverage review accepts the exclusion.
    """
    import re
    census = extract(archive)
    covered = excluded = 0
    unreviewed, listed = {}, []
    reasons = {}
    for item in census['items']:
        path, symbol = item['source'], item['symbol']
        if (path, symbol) in cites and (path, symbol) not in route_excluded:
            covered += 1
            continue
        if (path, symbol) not in route_excluded:
            for rule in exclusions:
                if re.search(rule['path'], path) and re.search(rule.get('symbol', ''), symbol):
                    excluded += 1
                    reasons[rule['reason']] = reasons.get(rule['reason'], 0) + 1
                    break
            else:
                unreviewed[path] = unreviewed.get(path, 0) + 1
                listed.append([path, symbol, item['line']])
            continue
        unreviewed[path] = unreviewed.get(path, 0) + 1
        listed.append([path, symbol, item['line']])
    return {'census_sha256': sha(json.dumps(census, sort_keys=True, separators=(',', ':')).encode()),
            'items': len(census['items']), 'cited': covered, 'excluded_by_rule': excluded,
            'exclusion_reasons': dict(sorted(reasons.items())),
            'unreviewed': sum(unreviewed.values()), 'unreviewed_by_source': dict(sorted(unreviewed.items())),
            'unreviewed_items': listed}


def dump_atomic(result):
    """One child per line, so a review diff shows each changed obligation."""
    head = {k: v for k, v in result.items() if k != 'children'}
    lines = json.dumps(head, indent=1, sort_keys=True)[:-2]
    body = ',\n'.join(json.dumps(c, sort_keys=True, ensure_ascii=False) for c in result['children'])
    return f'{lines},\n "children": [\n{body}\n ]\n}}\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--atomic', action='store_true',
                        help='regenerate the atomic acceptance cases from the rules and manual cases')
    parser.add_argument('--check', action='store_true',
                        help='with --atomic: fail if the committed atomic-cases.json differs from regeneration')
    args = parser.parse_args()
    if args.atomic:
        text = dump_atomic(atomic_cases(args.archive))
        target = INVENTORY / 'atomic-cases.json'
        if args.check:
            same = target.exists() and target.read_text() == text
            print('atomic cases match the pinned source' if same else 'atomic-cases.json is stale')
            return 0 if same else 1
        target.write_text(text)
        data = json.loads(text)
        print(json.dumps({'children': len(data['children']), 'groups': len(data['groups']),
                          'census': {k: v for k, v in data['census'].items()
                                     if k not in ('unreviewed_by_source', 'unreviewed_items')}}))
        return 0
    if args.output is None:
        parser.error('--output is required for the census')
    result = extract(args.archive)
    # Never overwrite a previously reviewed source identity.
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'sources': len(result['sources']), 'items': len(result['items']),
                      'qualification_eligible': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

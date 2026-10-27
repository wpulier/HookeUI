"""Source-census refusal cases; these do not qualify any viewer interaction."""
import copy
import json
import zipfile

import pytest

from bench import scientific_inventory as inventory


def test_properties_setters_platform_definitions_and_private_callbacks_are_visible():
    source = '''
class Viewer:
    axis: int = 0
    @property
    def current(self): return self.axis
    @current.setter
    def current(self, value): self.axis = value
    @register_action("move")
    def _move(self): pass
if PLATFORM:
    def platform_only(): pass
events = EmitterGroup(changed=Event, removed=Event)
'''
    rows = inventory.declarations(source)
    pairs = {(row['symbol'], row['kind']) for row in rows}
    assert {('Viewer.current', 'property'), ('Viewer.current', 'setter'),
            ('Viewer._move', 'registered-callback'), ('platform_only', 'callable'),
            ('EmitterGroup.changed', 'event'), ('EmitterGroup.removed', 'event'),
            ('Viewer.axis', 'field')} <= pairs


def test_archive_identity_checked_before_source_is_read(tmp_path):
    path = tmp_path/'wrong.zip'
    path.write_bytes(b'not the supplied source')
    with pytest.raises(inventory.InvalidInventory, match='pinned'):
        inventory.extract(path)


def test_census_is_deterministic_and_includes_examples_and_tests(tmp_path, monkeypatch):
    path = tmp_path/'fixture.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for file in ('src/napari/viewer.py', 'examples/new.py', 'src/napari/_tests/test_new.py'):
            archive.writestr('napari-main/'+file, 'def example(): pass\n')
    monkeypatch.setattr(inventory, 'ARCHIVE_SHA256', inventory.sha(path.read_bytes()))
    result = inventory.extract(path)
    assert result == inventory.extract(path)
    assert len(result['sources']) == len(result['items']) == 3
    assert not result['qualification_eligible']


@pytest.fixture
def coverage():
    census = {'items': [{'id': 'a'}, {'id': 'b'}]}
    review = {'format': 1, 'inventory_sha256': inventory.sha(json.dumps(census, sort_keys=True, separators=(',', ':')).encode()),
              'items': [{'id': 'a', 'disposition': 'required', 'group': 'P1.axis-position',
                         'owner': 'H04', 'behavior': 'Set current depth', 'hooke_interface': 'Depth control',
                         'upstream_limits': 'Within axis bounds',
                         'cases': [{'id': 'P1.axis-position.depth', 'procedure': 'Enter depth and reopen',
                                    'oracle': 'Native selected plane', 'criteria': 'Exact plane equality',
                                    'entrypoint': 'tests/test_depth.py'}]},
                        {'id': 'b', 'disposition': 'excluded', 'reason': 'Private diagnostic helper'}]}
    return census, review


def test_structural_coverage_does_not_authenticate_itself(coverage):
    result = inventory.validate_review(*coverage, {'P1.axis-position'})
    assert result['atomic_cases'] == 1 and not result['qualification_eligible']
    assert result['independent_review'] == 'required'


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'extra', 'stale', 'no-reason',
                                  'no-case', 'null-criteria', 'no-interface', 'unknown-group', 'duplicate-case'])
def test_incomplete_source_review_is_refused(coverage, change):
    census, review = coverage
    rows = review['items']
    if change == 'missing': rows.pop()
    elif change == 'duplicate': rows.append(copy.deepcopy(rows[0]))
    elif change == 'extra': rows.append({'id': 'c'})
    elif change == 'stale': review['inventory_sha256'] = '0'*64
    elif change == 'no-reason': rows[1]['reason'] = ''
    elif change == 'no-case': rows[0]['cases'] = []
    elif change == 'null-criteria': rows[0]['cases'][0]['criteria'] = None
    elif change == 'no-interface': rows[0]['hooke_interface'] = ''
    elif change == 'unknown-group': rows[0]['group'] = 'P1.omitted'
    elif change == 'duplicate-case': rows[0]['cases'].append(copy.deepcopy(rows[0]['cases'][0]))
    with pytest.raises(inventory.InvalidInventory):
        inventory.validate_review(census, review, {'P1.axis-position'})


SOURCE = '''
class Mode(StringEnum):
    PAINT = auto()
    PICK = 'pick'

class Layer:
    events = None
    def __init__(self):
        self.events = EmitterGroup(source=self, opacity=Event, _private=Event)
    @property
    def opacity(self): return 1
    @opacity.setter
    def opacity(self, value): pass
    @property
    def thumbnail(self): return None
    def refresh(self): pass
    def _hidden(self): pass

@register_layer_action('Paint the label')
def activate_paint_mode(layer): pass

for mode in ('max', 'min'):
    ACTIONS.append(Action(id=f'napari.layer.project_{mode}', title=f'{mode} projection', callback=partial(_project, mode=mode)))
'''


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    path = tmp_path / 'fixture.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('napari-main/src/napari/layers/layer.py', SOURCE)
        archive.writestr('napari-main/src/napari/layers/_tests/test_layer.py', 'def test_layer(): pass\n')
    monkeypatch.setattr(inventory, 'ARCHIVE_SHA256', inventory.sha(path.read_bytes()))
    layer = 'src/napari/layers/layer.py'
    rules = {'evidence_defaults': {f: 'isolated_profile_browser' for f in
                                   ('P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7', 'P8', 'Q', 'W',
                                    'R01', 'R02', 'R03', 'R04', 'R05', 'R06', 'R07', 'R08', 'R09')},
             'group_evidence': {}, 'limits': {'P3.common-appearance': ['An upstream limit']},
             'census_exclusions': [{'path': '/_tests/', 'reason': 'Upstream tests'},
                                   {'path': '.', 'symbol': '(^|\\.)_[^_]', 'reason': 'Private helper'}],
             'selectors': [
                 {'source': layer, 'select': 'enum', 'class': 'Mode', 'kind': 'enum-value', 'group': 'P4.label-tools'},
                 {'source': layer, 'select': 'properties', 'class': 'Layer', 'kind': 'property',
                  'routes': [['thumbnail', 'exclude', 'Rendered preview only']], 'group': 'P3.common-appearance'},
                 {'source': layer, 'select': 'methods', 'class': 'Layer', 'kind': 'method', 'group': 'P5.fast-navigation'},
                 {'source': layer, 'select': 'events', 'class': 'Layer', 'kind': 'event', 'group': 'P6.callbacks'},
                 {'source': layer, 'select': 'registered', 'kind': 'action', 'group': 'P4.label-tools'},
                 {'source': layer, 'select': 'actions', 'kind': 'menu-command', 'group': 'P1.slab-projection'}],
             'templates': [{'suffix': 'qualify-usable', 'families': list(rules_families()), 'basis': 'test',
                            'requirement': '"{title}" is usable', 'evidence': 'fresh_researcher_comparison'}]}
    manual = {'children': [{'id': 'P4.label-tools.encircle-start', 'group': 'P4.label-tools', 'kind': 'procedure',
                            'requirement': 'Start a stroke', 'basis': 'ticket 46 A6', 'cites': [[layer, 'activate_paint_mode']],
                            'procedure': 'Right press', 'oracle': 'Upstream test'}]}
    return path, rules, manual


def rules_families():
    return ('P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7', 'P8', 'Q', 'W',
            'R01', 'R02', 'R03', 'R04', 'R05', 'R06', 'R07', 'R08', 'R09')


def test_atomic_children_come_from_the_pinned_source_and_are_deterministic(pinned):
    archive, rules, manual = pinned
    result = inventory.atomic_cases(archive, rules, manual)
    assert result == inventory.atomic_cases(archive, rules, manual)
    children = {c['id']: c for c in result['children']}
    assert {'P4.label-tools.mode-paint', 'P4.label-tools.mode-pick', 'P3.common-appearance.layer-opacity',
            'P5.fast-navigation.layer-refresh', 'P6.callbacks.layer-events-opacity', 'P4.label-tools.activate-paint-mode',
            'P1.slab-projection.napari-layer-project-max', 'P1.slab-projection.napari-layer-project-min',
            'P4.label-tools.encircle-start'} <= set(children)
    assert children['P4.label-tools.mode-paint']['detail'] == {'value': 'paint'}
    assert children['P3.common-appearance.layer-opacity']['upstream_limits'] == ['An upstream limit']
    assert 'changing it has the upstream effect' in children['P3.common-appearance.layer-opacity']['requirement']
    assert not any('hidden' in i or 'private' in i or 'thumbnail' in i for i in children)
    assert result['excluded'][0]['reason'] == 'Rendered preview only'
    assert len(result['groups']) == 80 and all(g['children'] for g in result['groups'].values())
    # Exact citation only: uncited members, dunders and the route-excluded thumbnail stay unreviewed.
    unreviewed = {symbol for _, symbol, _ in result['census']['unreviewed_items']}
    assert {'Layer.thumbnail', 'Layer.__init__'} <= unreviewed and 'Layer.opacity' not in unreviewed
    assert result['census']['exclusion_reasons'] == {'Private helper': 2, 'Upstream tests': 1}  # _hidden, _private
    assert not result['qualification_eligible']
    text = inventory.dump_atomic(result)
    assert json.loads(text) == json.loads(json.dumps(result))


def test_a_work_package_specifies_a_generated_child_without_replacing_anything(pinned):
    archive, rules, manual = pinned
    spec = {'procedure': 'Change opacity in the Studio', 'oracle': 'Pinned napari', 'criteria': 'Exact',
            'hooke_equivalent': 'Layer opacity'}
    manual['specifications'] = {'P3.common-appearance.layer-opacity': spec}
    children = {c['id']: c for c in inventory.atomic_cases(archive, rules, manual)['children']}
    assert {k: children['P3.common-appearance.layer-opacity'][k] for k in spec} == spec
    assert 'procedure' not in children['P5.fast-navigation.layer-refresh']
    for bad in ({'procedure': 'x', 'oracle': 'y'}, {**spec, 'status': 'pass'}, {**spec, 'criteria': ' '},
                {**spec, 'evidence_class': 'source_inspection'}):
        manual['specifications'] = {'P3.common-appearance.layer-opacity': bad}
        with pytest.raises(inventory.InvalidInventory):
            inventory.atomic_cases(archive, rules, manual)
    manual['specifications'] = {'P4.label-tools.encircle-start': {'procedure': 'x', 'oracle': 'y', 'criteria': 'z'}}
    with pytest.raises(inventory.InvalidInventory, match='already specified'):
        inventory.atomic_cases(archive, rules, manual)
    # A written procedure and oracle stay; the work package adds only what is missing.
    manual['specifications'] = {'P4.label-tools.encircle-start': {'criteria': 'Every mapped test passes'}}
    child = next(c for c in inventory.atomic_cases(archive, rules, manual)['children'] if c['id'] == 'P4.label-tools.encircle-start')
    assert (child['procedure'], child['criteria']) == ('Right press', 'Every mapped test passes')
    manual['specifications'] = {'P3.common-appearance.layer-opacity': {'criteria': 'z'}}
    with pytest.raises(inventory.InvalidInventory, match='must leave'):
        inventory.atomic_cases(archive, rules, manual)
    manual['specifications'] = {'P3.common-appearance.absent': {'procedure': 'x', 'oracle': 'y', 'criteria': 'z'}}
    with pytest.raises(inventory.InvalidInventory, match='absent child'):
        inventory.atomic_cases(archive, rules, manual)


@pytest.mark.parametrize('change', ['unrouted', 'found-nothing', 'no-source', 'exclusion-reason', 'unknown-group',
                                    'absent-cite', 'duplicate', 'template-gap', 'override-gap'])
def test_an_item_cannot_drop_out_silently(pinned, change):
    archive, rules, manual = pinned
    if change == 'unrouted': rules['selectors'][1].pop('group')
    elif change == 'found-nothing': rules['selectors'][0]['class'] = 'Layer'
    elif change == 'no-source': rules['selectors'][0]['source'] = 'src/napari/absent.py'
    elif change == 'exclusion-reason': rules['selectors'][1]['routes'] = [['thumbnail', 'exclude']]
    elif change == 'unknown-group': rules['selectors'][0]['group'] = 'P4.label-everything'
    elif change == 'absent-cite': manual['children'][0]['cites'] = [['src/napari/layers/layer.py', 'invented']]
    elif change == 'duplicate': manual['children'][0]['id'] = 'P4.label-tools.mode-paint'
    elif change == 'template-gap': rules['templates'][0]['families'] = ['P1']
    elif change == 'override-gap': rules['child_evidence'] = {'P4.label-tools.absent': 'source_inspection'}
    with pytest.raises(inventory.InvalidInventory):
        inventory.atomic_cases(archive, rules, manual)


def test_a_harness_states_its_hooke_equivalent_and_gives_it_to_its_children(pinned):
    archive, rules, manual = pinned
    child = next(c for c in inventory.atomic_cases(archive, rules, manual)['children']
                 if c['id'] == 'P3.common-appearance.layer-opacity')
    harness = {'name': 'example', 'families': ['P3'], 'kinds': [child['kind']], 'symbols': [child['source']['symbol']],
               'evidence': child['evidence_class'], 'basis': child['basis'], 'procedure': 'Run it', 'oracle': 'Compare',
               'criteria': 'Passes'}
    rules['harnesses'] = [harness]
    with pytest.raises(inventory.InvalidInventory, match='states no hooke_equivalent'):
        inventory.atomic_cases(archive, rules, manual)
    harness['hooke_equivalent'] = 'The Opacity control of the layer'
    children = {c['id']: c for c in inventory.atomic_cases(archive, rules, manual)['children']}
    assert children['P3.common-appearance.layer-opacity']['hooke_equivalent'] == 'The Opacity control of the layer'


def test_a_specification_readies_a_generated_child_and_nothing_else(pinned):
    archive, rules, manual = pinned
    spec = {'id': 'P3.common-appearance.layer-opacity', 'procedure': 'Change opacity in the browser',
            'oracle': 'Pinned napari and the definition', 'criteria': 'Equal opacity', 'hooke_equivalent': 'View > Opacity'}
    result = inventory.atomic_cases(archive, rules, {**manual, 'specifications': {spec['id']: {k: v for k, v in spec.items() if k != 'id'}}})
    child = next(c for c in result['children'] if c['id'] == spec['id'])
    assert {k: child[k] for k in spec} == spec
    assert child['evidence_class'] == 'isolated_profile_browser' and 'status' not in child and 'evidence' not in child
    assert result['manual_sha256'] != inventory.atomic_cases(archive, rules, manual)['manual_sha256']


@pytest.mark.parametrize('change', ['absent', 'twice', 'evidence-class', 'status', 'empty', 'differs', 'nothing'])
def test_a_specification_cannot_relabel_pass_or_overwrite(pinned, change):
    archive, rules, manual = pinned
    spec = {'id': 'P3.common-appearance.layer-opacity', 'criteria': 'Equal opacity'}
    specs = [spec]
    if change == 'absent': spec['id'] = 'P3.common-appearance.invented'
    elif change == 'twice': specs = [spec, dict(spec)]
    elif change == 'evidence-class': spec['evidence_class'] = 'synthetic_fixture'
    elif change == 'status': spec['status'] = 'pass'
    elif change == 'empty': spec['criteria'] = ' '
    elif change == 'differs': specs = [{'id': 'P4.label-tools.encircle-start', 'oracle': 'Something else'}]
    elif change == 'nothing': specs = [{'id': spec['id']}]
    with pytest.raises(inventory.InvalidInventory):
        inventory.atomic_cases(archive, rules, {**manual, 'specifications': specs})


@pytest.mark.parametrize('redirect', ['child-A', 'child-B'])
def test_dictionary_specification_cannot_override_its_authoritative_child_key(redirect):
    children = [{'id': 'child-A'}, {'id': 'child-B'}]
    specifications = {'child-A': {'id': redirect, 'procedure': 'Run', 'oracle': 'Compare', 'criteria': 'Equal'}}
    with pytest.raises(inventory.InvalidInventory):
        inventory.specify(children, specifications)
    assert children == [{'id': 'child-A'}, {'id': 'child-B'}]


def test_h07_specification_writer_preserves_current_dictionary_and_other_lanes(tmp_path, monkeypatch):
    from bench import scientific_specs_h07 as h07
    path = tmp_path / 'manual.json'
    retained = {'P1.kept': {'procedure': 'Open', 'oracle': 'Independent', 'criteria': 'Exact'},
                'R01.kept': {'criteria': 'Preserve provenance'}}
    path.write_text(json.dumps({'children': [], 'specifications': retained}))
    monkeypatch.setattr(h07, 'MANUAL', path)
    h07.main()
    result = json.loads(path.read_text())['specifications']
    assert type(result) is dict
    assert {key: result[key] for key in retained} == retained
    assert set(result) - set(retained) == set(h07.checks.MAPPING)
    assert all('id' not in fields for fields in result.values())

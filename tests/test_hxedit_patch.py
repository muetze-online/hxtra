"""Tests of hxedit_patch on a small made-up catalog in HX Edit's file formats (no Line 6 data)."""
import json
import os
import struct

import msgpack
import pytest

import hxedit_patch as hp


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def ibits(n):
    """An int or bool knob's value as Entry 3 keeps it: the bits of a float32."""
    return struct.unpack('<f', struct.pack('<I', n))[0]


def knob(name, t=3, lo=0.0, hi=1.0, de=0.5, **meta):
    if t in (1, 2):
        lo, hi, de = ibits(int(lo)), ibits(int(hi)), ibits(int(de))
    return {'name': name, 'type': t, 'slot': 0, 'min': f32(lo), 'max': f32(hi), 'default': f32(de),
            'displayName': meta.get('displayName', ''), 'meta': meta}


def model(name, params, sidx=1):
    return {'name': name, 'model_id': sidx, 'words': [0, 0, sidx], 'params': params}


DB = dict(displayType='db')
HZ = dict(displayType='hz', valueMinString='Off')
FX = {'displayType': 'list', '0': 'Full', '1': 'Xover', 'displayName': 'Mode'}
GAIN = lambda: knob('Gain', displayType='knob')
LEVEL = lambda: knob('Level', lo=-60, hi=6, de=0, **DB)

STOCK = [
    model('HD2_FooMono', [GAIN(), LEVEL()]),
    model('HD2_FooStereo', [GAIN(), LEVEL()]),
    model('HD2_BarMono', [GAIN(), knob('Smart', de=0.5)]),
    model('HD2_BazMono', [GAIN()]),
    model('HD2_BazStereo', [GAIN()]),
    model('HD2_QuxMono', [GAIN()]),
]
NEW = lambda: [knob('Frequency', lo=19.9, hi=1000, de=19.9, **HZ), knob('Wet Level', lo=-60, hi=9, de=0, **DB),
               knob('Mode', t=2, lo=0, hi=1, de=0, **FX)]
IMAGE = [
    model('HD2_FooMono', [GAIN(), LEVEL()] + NEW()),         # extended, mono only
    model('HD2_FooStereo', [GAIN(), LEVEL()]),
    model('HD2_BarMono', [GAIN()] + NEW() + [knob('Smart', de=0.5)]),   # new knobs before a hidden last one
    model('HD2_BazMono', [GAIN()]),
    model('HD2_BazStereo', [GAIN(), knob('Gate', t=1, lo=0, hi=0, de=0, false='Off', true='On')]),  # stereo only; Entry 3 keeps an on/off knob's max as 0
    model('HD2_QuxMono', [GAIN(), knob('Level', lo=-60, hi=6, de=0, **DB)]),  # name clash with HX Edit's knob
    model('HD2_NewOne', [GAIN()]),
]

SYM = [('HD2_FooMono', ['Gain', 'Level']), ('HD2_FooStereo', ['Gain', 'Level']), ('HD2_BarMono', ['Gain', 'Smart']),
       ('HD2_BazMono', ['Gain']), ('HD2_BazStereo', ['Gain']), ('HD2_QuxMono', ['Gain_X']), ('HD2_Other', ['A'])]


def z(s):
    return s.encode() + b'\0'


def zd(d):
    return {z(k): (z(v) if isinstance(v, str) else v) for k, v in d.items()}


def pdef(sid, name, dt, lo=0.0, hi=1.0, de=0.5, vt=1):
    return zd({'name': name, 'min': lo, 'valueType': vt, 'symbolicID': sid, 'displayType': dt, 'default': de, 'max': hi})


EN = zd({'symbolicID': '@enabled', 'name': 'Enabled', 'valueType': 2, 'max': True, 'default': True, 'min': False})
DEFS = [
    zd({'name': 'Foo', 'symbolicID': 'HD2_Foo', 'load': 1.5}) | {z('params'): [
        pdef('Gain', 'Gain', 'generic_knob'), pdef('Level', 'Level', 'volume', -60.0, 6.0, 0.0), EN]},
    zd({'name': 'Bar', 'symbolicID': 'HD2_Bar'}) | {z('params'): [pdef('Gain', 'Gain', 'generic_knob'), EN]},
    zd({'name': 'Baz', 'symbolicID': 'HD2_Baz'}) | {z('params'): [pdef('Gain', 'Gain', 'generic_knob'), EN]},
    zd({'name': 'Qux', 'symbolicID': 'HD2_Qux'}) | {z('params'): [pdef('Gain_X', 'Gain', 'generic_knob'),
                                                                  pdef('Level', 'Level', 'volume'), EN]},
]
CAT = {'categories': [{'name': 'Distortion', 'subcategories': [
    {'name': 'Mono', 'models': [{'id': 'HD2_Foo', 'name': 'Foo', 'image': 'f.png', 'params': [{'Gain': None}, {'Level': 'Lvl'}]},
                                {'id': 'HD2_Bar', 'name': 'Bar', 'image': 'b.png', 'params': [{'Gain': None}]},
                                {'id': 'HD2_Baz', 'name': 'Baz', 'image': 'z.png', 'params': [{'Gain': None}]}]},
    {'name': 'Stereo', 'models': [{'id': 'HD2_Foo', 'name': 'Foo', 'image': 'f.png', 'params': [{'Gain': None}, {'Level': 'Lvl'}]},
                                  {'id': 'HD2_Baz', 'name': 'Baz', 'image': 'z.png', 'params': [{'Gain': None}]}]}]}]}
CONTROLS = {'generic_knob': {'format': '%.1f'}, 'volume': {'format': '%+.1f'},
            'mod_low_cut': {'format': [{'lowerBound': 0.0, 'upperBound': 20.0, 'format': 'Off'}]},
            'off_on': {'isDiscrete': True, 'controlType': 'segmented', 'format': ['Off', 'On']}}


def sym_text(entries):
    return '[\n' + ',\n'.join('\t{\n\t\t"symbol": %s,\n\t\t"parameters": [ %s ]\n\t}' % (
        json.dumps(s), ', '.join(json.dumps(n) for n in p)) for s, p in entries) + '\n]\n'


def cat_json_text(cat):
    """HX_ModelCatalog.json in HX Edit's layout: one { "knob": label } per line."""
    def model_text(m, ind):
        params = ',\n'.join(ind + '    { %s: %s }' % (json.dumps(k), json.dumps(v)) for e in m['params'] for k, v in e.items())
        return (ind + '{\n' + ind + '  "id": %s,\n' % json.dumps(m['id']) + ind + '  "name": %s,\n' % json.dumps(m['name'])
                + ind + '  "image": %s,\n' % json.dumps(m['image']) + ind + '  "params": [\n' + params + '\n'
                + ind + '  ]\n' + ind + '}')
    out = '{\n  "categories": [\n'
    cats = []
    for c in cat['categories']:
        subs = ',\n'.join('        {\n          "name": %s,\n          "models": [\n%s\n          ]\n        }' % (
            json.dumps(s['name']), ',\n'.join(model_text(m, '            ') for m in s['models'])) for s in c['subcategories'])
        cats.append('    {\n      "name": %s,\n      "subcategories": [\n%s\n      ]\n    }' % (json.dumps(c['name']), subs))
    return out + ',\n'.join(cats) + '\n  ]\n}\n'


def zcat(o):
    if isinstance(o, dict):
        return {z(k): (None if v is None else z(v) if isinstance(v, str) else zcat(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [zcat(v) for v in o]
    return o


@pytest.fixture
def app(tmp_path):
    a = tmp_path / 'HX Edit copy.app'
    r = a / 'Contents' / 'Resources'
    r.mkdir(parents=True)
    (r / 'Helix.sym').write_text(sym_text(SYM))
    (r / 'HelixModelDefs.bin').write_bytes(hp.pack(DEFS))
    (r / 'HX_ModelCatalog.bin').write_bytes(hp.pack(zcat(CAT)))
    (r / 'HX_ModelCatalog.json').write_text(cat_json_text(CAT))
    assert json.loads((r / 'HX_ModelCatalog.json').read_text()) == CAT
    (r / 'HelixControls.json').write_text(json.dumps(CONTROLS, indent=2) + '\n')
    for n in ('stock.hxf', 'image.hxf', 'other.hxf'):
        (tmp_path / n).write_bytes(n.encode())
    return a


@pytest.fixture(autouse=True)
def fake_firmware(monkeypatch):
    other = [m if m['name'] != 'HD2_FooMono' else model('HD2_FooMono', [GAIN(), LEVEL(), NEW()[0]]) for m in IMAGE]
    table = {'stock.hxf': STOCK, 'image.hxf': IMAGE, 'other.hxf': other}
    monkeypatch.setattr(hp, 'e3_models', lambda p: table[os.path.basename(str(p))])
    # preset ids: the stock models at Helix.sym 0..5, the image's new model at 6 (HD2_Other's place)
    pids = {m['name']: i for i, m in enumerate(STOCK)} | {'HD2_NewOne': 6}
    monkeypatch.setattr(hp, 'model_pids', lambda hxf, models: {m['name']: pids[m['name']] for m in models})


def files(app):
    r = app / 'Contents' / 'Resources'
    return {p.name: p.read_bytes() for p in r.iterdir()}


def patch(app, image='image.hxf', labels='own'):
    d = app.parent
    return hp.patch_app(str(app), str(d / image), str(d / 'stock.hxf'), labels, sign=False, log=lambda *a: None)


def test_round_trip_byte_equal():
    raw = hp.pack(DEFS)
    assert hp.pack(hp.unpack(raw)) == raw
    raw = msgpack.packb(zcat(CAT), use_bin_type=False, use_single_float=True)
    assert hp.pack(hp.unpack(raw)) == raw


def test_unknown_msgpack_form_refused():
    raw = msgpack.packb([1.5], use_single_float=False)       # a float64 HX Edit's files do not use
    with pytest.raises(hp.PatchError):
        hp.unpack(raw)


def test_added_knobs_keeps_stock_order():
    add = hp.added_knobs(STOCK, IMAGE)
    assert add[None] == ['HD2_NewOne']
    assert add['HD2_BarMono'][2] == [1, 2, 3]                # before Smart
    assert 'HD2_FooStereo' not in add
    with pytest.raises(hp.PatchError):
        hp.added_knobs(STOCK, [model('HD2_FooMono', [LEVEL(), GAIN()])])


def test_patch(app):
    before = files(app)
    plan = patch(app)
    after = files(app)
    cat = hp.Catalog(str(app / 'Contents' / 'Resources'))
    # Helix.sym: the mono variant names the image's values in its order, the stereo one stays, nothing else moves
    assert cat.sym['HD2_FooMono'] == ['Gain', 'Level', 'Frequency', 'Wet Level', 'Mode']
    assert cat.sym['HD2_FooStereo'] == ['Gain', 'Level']
    assert cat.sym['HD2_BarMono'] == ['Gain', 'Frequency', 'Wet Level', 'Mode', 'Smart']
    assert cat.sym['HD2_BazStereo'] == ['Gain', 'Gate']
    old, new = before['Helix.sym'].decode().split('\n'), after['Helix.sym'].decode().split('\n')
    assert len(old) == len(new) and sum(a != b for a, b in zip(old, new)) == 3
    # HelixModelDefs.bin: the knobs before @enabled, typed as HX Edit keeps them
    names = cat.def_names('HD2_Foo')
    assert names == ['Gain', 'Level', 'Frequency', 'Wet Level', 'Mode', '@enabled']
    p = {hp.s_(d[z('symbolicID')]): {hp.s_(k): hp.s_(v) for k, v in d.items()} for d in cat.def_params('HD2_Foo')}
    assert p['Frequency']['displayType'] == 'generic_knob'      # no stock knob described alike: a float knob
    assert p['Wet Level']['displayType'] == 'volume'                     # learned from stock Level (dB)
    assert p['Mode'] == {'name': 'Mode', 'min': 0, 'valueType': 0, 'symbolicID': 'Mode',
                         'displayType': 'hxtra_full_xover', 'default': 0, 'max': 1}
    assert p['Frequency']['default'] == f32(19.9)
    assert cat.def_names('HD2_Bar') == ['Gain', 'Frequency', 'Wet Level', 'Mode', '@enabled']
    gate = {hp.s_(k): hp.s_(v) for k, v in cat.def_params('HD2_Baz')[1].items()}
    assert gate['stereo-only'] is True and gate['valueType'] == 2 and gate['displayType'] == 'off_on'
    assert gate['min'] is False and gate['max'] is True and gate['default'] is False   # else HX Edit shows only 'Off'
    # the edit view: the Mono occurrence of Foo lists the new knobs, the Stereo one does not
    occ = {sub: [hp.s_(next(iter(e))) for e in m[z('params')]] for sub, m in cat.bin_occ('HD2_Foo')}
    assert occ == {'Mono': ['Gain', 'Level', 'Frequency', 'Wet Level', 'Mode'], 'Stereo': ['Gain', 'Level']}
    occ = {sub: [next(iter(e)) for e in m['params']] for sub, m in cat.json_occ('HD2_Foo')}
    assert occ == {'Mono': ['Gain', 'Level', 'Frequency', 'Wet Level', 'Mode'], 'Stereo': ['Gain', 'Level']}
    occ = {sub: [next(iter(e)) for e in m['params']] for sub, m in cat.json_occ('HD2_Baz')}
    assert occ == {'Mono': ['Gain'], 'Stereo': ['Gain', 'Gate']}
    assert cat.json_occ('HD2_Foo')[0][1]['params'][1] == {'Level': 'Lvl'}          # labels kept
    # the json keeps its layout: only lines of patched models are added or changed
    old, new = before['HX_ModelCatalog.json'].decode(), after['HX_ModelCatalog.json'].decode()
    assert new.count('\n') == old.count('\n') + 7            # Foo mono 3 knobs, Bar 3, Baz stereo 1
    # own display type for Full/Xover, stock file otherwise unchanged
    hc = json.loads(after['HelixControls.json'])
    assert hc['hxtra_full_xover'] == {'isDiscrete': True, 'controlType': 'segmented', 'format': ['Full', 'Xover']}
    assert {k: v for k, v in hc.items() if k in CONTROLS} == CONTROLS
    assert after['HelixControls.json'].startswith(before['HelixControls.json'].rstrip()[:-1].rstrip())
    # skipped: a name clash (Qux), a model HX Edit lacks
    assert 'HD2_QuxMono' in plan['skipped'] and plan['new_models'] == ['HD2_NewOne']
    assert cat.sym['HD2_QuxMono'] == ['Gain_X']
    bad = hp.check(str(app), str(app.parent / 'image.hxf'), str(app.parent / 'stock.hxf'))
    assert len(bad) == 1 and bad[0].startswith('HD2_QuxMono')        # the skipped model stays a misfit


def test_check_finds_unpatched(app):
    bad = hp.check(str(app), str(app.parent / 'image.hxf'), str(app.parent / 'stock.hxf'))
    assert any('HD2_FooMono' in b for b in bad) and any('edit view lacks' in b for b in bad)


def test_idempotent(app):
    patch(app)
    once = files(app)
    assert patch(app) is None
    assert files(app) == once
    with pytest.raises(hp.PatchError):
        patch(app, 'other.hxf')                              # another build: start from a fresh copy
    with pytest.raises(hp.PatchError):
        patch(app, labels='stock')


def test_labels_stock(app):
    patch(app, labels='stock')
    cat = hp.Catalog(str(app / 'Contents' / 'Resources'))
    mode = next(d for d in cat.def_params('HD2_Foo') if hp.s_(d[z('symbolicID')]) == 'Mode')
    assert hp.s_(mode[z('displayType')]) == 'off_on'
    assert 'hxtra_full_xover' not in cat.controls


def test_sym_count_mismatch_skipped(app):
    r = app / 'Contents' / 'Resources' / 'Helix.sym'
    r.write_text(sym_text([(s, p + ['Extra'] if s == 'HD2_FooMono' else p) for s, p in SYM]))
    plan = patch(app)
    assert 'positions unclear' in plan['skipped']['HD2_FooMono']
    assert 'HD2_FooMono' not in plan['sym']


def test_probe_cases_typed():
    cases = {n: (v, s) for n, v, s in hp.probe_cases(STOCK, IMAGE, all_models=True)}
    v, s = cases['HD2_FooMono']
    assert v[-1] == 0 and type(v[-1]) is int and type(v[2]) is float and len(s) == 2
    assert cases['HD2_BazStereo'][0][-1] is False
    assert [hp.fmt_value(x) for x in (True, 3, 0.5, f32(19.9))] == ['true', '3', '0.5', repr(f32(19.9))]
    one = hp.probe_cases(STOCK, IMAGE)
    assert {n for n, _, _ in one} >= {'HD2_FooMono', 'HD2_BarMono', 'HD2_BazStereo'}


def test_probe_cases_prefer():
    import copy
    image = copy.deepcopy(IMAGE)
    twin = copy.deepcopy(next(m for m in image if m['name'] == 'HD2_FooMono'))
    twin['name'] = 'HD2_FooTwinMono'
    stock = copy.deepcopy(STOCK) + [copy.deepcopy(next(m for m in STOCK if m['name'] == 'HD2_FooMono'))]
    stock[-1]['name'] = 'HD2_FooTwinMono'
    image.append(twin)
    names = lambda cases: {n for n, _, _ in cases}
    assert 'HD2_FooMono' in names(hp.probe_cases(stock, image))
    picked = names(hp.probe_cases(stock, image, prefer=lambda n: n == 'HD2_FooTwinMono'))
    assert 'HD2_FooTwinMono' in picked and 'HD2_FooMono' not in picked
    assert names(hp.probe_cases(stock, image, prefer=lambda n: False)) == names(hp.probe_cases(stock, image))


def test_block_values_and_count():
    m = {'params': [{'name': 'a'}, {'name': 'b'}, {'name': 'Smart'}], 'meta': {'presetParamCount': '2'}}
    assert hp.preset_count(m) == 2 and hp.preset_count({'params': [1, 2, 3]}) == 3
    stock = {'params': [{'name': 'a'}, {'name': 'b'}]}
    img = {'params': [{'name': 'a'}, {'name': 'b'}, {'name': 'c'}]}
    assert hp.block_values([1.0, 2.0, 3.0], stock, [0.0, 0.0, False], img) == [1.0, 2.0, 3.0, False]
    assert hp.block_values([1.0, 2.0, 3.0], stock, None, img) == [1.0, 2.0, 3.0]


def test_block_values_drops_hidden_slot():
    stock = {'params': [{'name': 'a', 'slot': 2}, {'name': 'Smart', 'slot': 10000}]}
    img = {'params': [{'name': 'a', 'slot': 2}, {'name': 'F', 'slot': 7}, {'name': 'Smart', 'slot': 10000}]}
    assert hp.block_values([0.5, 250.0, 1.0], stock, None, img) == [0.5, 250.0]
    assert hp.block_values([0.5, 1.0], stock, None) == [0.5]


NEW_ONE = [knob('Time', lo=0, hi=8, de=0.5, displayType='time', sync='1', coupledIndex='3'),
           knob('Accent', t=2, lo=1, hi=3, de=1, displayType='list', **{'1': 'Off', '2': '2', '3': '3'}),
           knob('SyncSelect1', t=2, lo=1, hi=2, de=1, displayType='list', **{'1': '1/1', '2': '1/2'}),
           knob('TempoSync1', t=1, lo=0, hi=0, de=1)]
LIKE_DEF = zd({'name': 'Like', 'symbolicID': 'HD2_Like', 'category': 8, 'load': 2.5}) | {z('params'): [
    pdef('SyncSelect1', 'Note Sync', 'sync_note', 1, 2, 1, vt=0), pdef('TempoSync1', 'Tempo Sync', 'off_on', False, True, False, vt=2), EN]}
LIKE_CAT = {'id': 'HD2_Like', 'name': 'Like', 'image': 'like.png',
            'params': [[{'TempoSync1': None}, {'SyncSelect1': 'Note Sync'}, {'Speed': None}]]}


@pytest.fixture
def new_app(app, monkeypatch):
    """The copy with a stock model to lend its look (HD2_Like) and an image whose HD2_NewOne is a synced model."""
    r = app / 'Contents' / 'Resources'
    cat = json.loads(json.dumps(CAT))
    cat['categories'][0]['subcategories'][0]['models'].append({k: v for k, v in LIKE_CAT.items() if k != 'params'} | {'params': []})
    text = cat_json_text(cat).replace('"params": [\n\n', '"params": [\n')
    cat['categories'][0]['subcategories'][0]['models'][-1] = LIKE_CAT
    text = text.replace('"image": "like.png",\n              "params": [\n              ]',
                        '"image": "like.png",\n              "params": [\n                [ { "TempoSync1": null }, '
                        '{ "SyncSelect1": "Note Sync" }, { "Speed": null } ]\n              ]')
    assert json.loads(text) == cat, text
    (r / 'HX_ModelCatalog.json').write_text(text)
    (r / 'HX_ModelCatalog.bin').write_bytes(hp.pack(zcat(cat)))
    (r / 'HelixModelDefs.bin').write_bytes(hp.pack(DEFS + [LIKE_DEF]))
    image = [m for m in IMAGE if m['name'] != 'HD2_NewOne'] + [model('HD2_NewOneMono', NEW_ONE)]
    monkeypatch.setattr(hp, 'e3_models', lambda p: {'stock.hxf': STOCK}.get(os.path.basename(str(p)), image))
    pids = {m['name']: i for i, m in enumerate(STOCK)} | {'HD2_NewOneMono': 6}
    monkeypatch.setattr(hp, 'model_pids', lambda hxf, models: {m['name']: pids[m['name']] for m in models})
    monkeypatch.setattr(hp, 'NEW_MODEL_PLACES', {'HD2_NewOneMono': ('Distortion', 'Mono', 'HD2_Like')})
    return app


def test_new_model(new_app):
    before = files(new_app)
    plan = patch(new_app)
    after = files(new_app)
    cat = hp.Catalog(str(new_app / 'Contents' / 'Resources'))
    assert plan['new_models'] == [] and 'HD2_NewOneMono' in plan['add']
    # Helix.sym: entry 6 (was HD2_Other) is the new model, one line pair changed, the list as long as before
    sym = json.loads(after['Helix.sym'])
    assert sym[6] == {'symbol': 'HD2_NewOneMono', 'parameters': ['Time', 'Accent', 'SyncSelect1', 'TempoSync1']}
    assert len(sym) == len(json.loads(before['Helix.sym'])) and 'HD2_Other' not in cat.sym
    # a definition under the name without Mono, mono only, the like model's category and load, @enabled last
    assert cat.defs_id('HD2_NewOneMono') == 'HD2_NewOne'
    d = {hp.s_(k): v for k, v in cat.defs_by_id['HD2_NewOne'].items()}
    assert (d['mono'], d['stereo'], d['category'], hp.s_(d['name'])) == (True, False, 8, 'HD2_NewOne')
    p = {hp.s_(x[z('symbolicID')]): {hp.s_(k): hp.s_(v) for k, v in x.items()} for x in d['params']}
    assert list(p) == ['Time', 'Accent', 'SyncSelect1', 'TempoSync1', '@enabled']
    assert p['SyncSelect1']['name'] == 'Note Sync' and p['SyncSelect1']['displayType'] == 'sync_note'
    assert p['Accent']['displayType'] == 'hxtra_off_2_3' and (p['Accent']['min'], p['Accent']['max']) == (1, 3)
    assert json.loads(after['HelixControls.json'])['hxtra_off_2_3']['format'] == ['Off', '2', '3']
    # the browser: the synced pair grouped with Time, the like model's picture, in both catalog files
    want = {'id': 'HD2_NewOne', 'name': 'HD2_NewOne', 'image': 'like.png',
            'params': [[{'TempoSync1': None}, {'SyncSelect1': 'Note Sync'}, {'Time': None}], {'Accent': None}]}
    assert cat.json_occ('HD2_NewOne')[0][1] == want
    assert cat.bin_occ('HD2_NewOne')[0][1] == zcat(want)
    bad = hp.check(str(new_app), str(new_app.parent / 'image.hxf'), str(new_app.parent / 'stock.hxf'))
    assert bad and all(b.startswith('HD2_QuxMono') for b in bad)     # only the skipped name clash
    assert json.loads(after['hxtra-knobs.json'])['added']['HD2_NewOneMono']['replaces'] == 'HD2_Other'


def test_new_model_on_stock_id_refused(new_app, monkeypatch):
    monkeypatch.setattr(hp, 'model_pids', lambda hxf, models: {m['name']: (5 if m['name'] == 'HD2_NewOneMono' else i)
                                                               for i, m in enumerate(models)})
    plan = patch(new_app)
    assert 'stock model' in plan['skipped']['HD2_NewOneMono'] and not plan['add']
    assert hp.Catalog(str(new_app / 'Contents' / 'Resources')).json_occ('HD2_NewOne') == []


def test_check_finds_missing_new_model(new_app):
    bad = hp.check(str(new_app), str(new_app.parent / 'image.hxf'), str(new_app.parent / 'stock.hxf'))
    assert any('HD2_NewOneMono: Helix.sym[6]' in b for b in bad) and any('block browser' in b for b in bad)

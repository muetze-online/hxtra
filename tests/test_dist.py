"""tools/patch_dist.py: mono distortions with test build D's Freq, Dry Level, Wet Level and Mode, every model in one pass."""
import struct

import pytest

import entry4_blob as eb
import firmware
import kit_patch_amp as kp
import patch_dist as pdist
import patch_teemah as pt

TEEMAH, SCREAM = 'HD2_DistTeemahMono', 'HD2_DistScream808Mono'


def _w4(l6):
    e4 = kp.get_entry_swapped(l6, 4)
    return struct.unpack('<%dI' % (len(e4) // 4), e4)


def _model(l6, name):
    return kp.find_model(kp.parse_entry3_full(kp.get_entry_swapped(l6, 3)), name)


def _members(b0, comp):
    ct = b0[0x10]
    h = b0[ct + 7 * (comp - 0x600000): ct + 7 * (comp - 0x600000) + 7]
    return h, [(b0[h[3] + 20 * m + 6], b0[h[3] + 20 * m + 4],
                b0[b0[h[3] + 20 * m + 8]: b0[h[3] + 20 * m + 8] + b0[h[3] + 20 * m + 7]],
                b0[b0[h[3] + 20 * m + 10]: b0[h[3] + 20 * m + 10] + b0[h[3] + 20 * m + 9]],
                [tuple(b0[b0[h[3] + 20 * m + 12] + 2 * k: b0[h[3] + 20 * m + 12] + 2 * k + 2])
                 for k in range(b0[h[3] + 20 * m + 11])]) for m in range(h[2])]


def _params(l6, name):
    return [(p['name'], p['type'], p['slot'], p['meta'], p['min'], p['max'], p['default'])
            for p in _model(l6, name)['params']]


@pytest.fixture(scope='module')
def built():
    """Stock, rollout D2 (Teemah! and Scream 808 alone), test build D3 (verified on the unit)."""
    stock = firmware.stock()
    return (stock, pdist.patch_dist(stock, (TEEMAH, SCREAM), log=lambda *_: None),
            pt.patch_teemah_d(stock, log=lambda *_: None))


@pytest.mark.firmware
def test_teemah_composite_is_test_build_d3s_with_the_dry_path_first(built):
    """Test build D3's members, its tap gone: the two low-passes right after the in-port, the first reading the
    in-port's buffer into the dry one (a tap carried the in-port buffer's final content in Clawthorn Drive and
    Bitcrusher on the unit, 2026-11-20); the drive path on a buffer of its own, which the first high-pass fills from
    the in-port's, so nothing after the in-port writes buffer 0 (the dry path got the high-passed input in Thrifter
    Fuzz and Screamer on the unit, 2026-11-21)."""
    _, new, d3 = built
    (bn, bd) = (eb.blob(_w4(x), 0) for x in (new, d3))
    hn, mn = _members(bn, eb.top_type(eb.blob(_w4(new), _model(new, TEEMAH)['blob_index'])))
    hd, md = _members(bd, 0x600000 + bd[0xf] - 1)
    tap = md[1]
    assert tap[:2] == (0, 1)
    mix = [k for k, m in enumerate(md) if m[0] == 0x84][0]
    lp_a, lp_b = md[mix - 2], md[mix - 1]
    assert lp_a[0] == lp_b[0] == 0x102 and lp_a[2] == tap[3]
    wet = hd[1]
    ren = lambda bufs: type(bufs)(wet if b == 0 else b for b in bufs)
    mv = lambda m: m[:2] + (ren(m[2]), ren(m[3])) + m[4:]
    hp1 = md[2][:3] + (ren(md[2][3]),) + md[2][4:]
    want = [md[0], lp_a[:2] + (tap[2],) + lp_a[3:], lp_b, hp1] + [mv(m) for m in [md[3]] + md[4: mix - 2] + md[mix:]]
    assert hn[1] == hd[1] + 1 and hn[2] == hd[2] - 1 and mn == want
    assert all(m[1] != 1 for m in mn[1:5])
    assert all(0 not in m[3] for m in mn[1:]) and mn[3][2:4] == ([0], [wet])


@pytest.mark.firmware
def test_teemah_parameters_are_test_build_d3s(built):
    _, new, d3 = built
    assert repr(_params(new, TEEMAH)) == repr(_params(d3, TEEMAH))


@pytest.mark.firmware
def test_scream_808_shares_teemahs_new_composite(built):
    stock, new, _ = built
    w = _w4(new)
    tops = {n: eb.top_type(eb.blob(w, _model(new, n)['blob_index'])) for n in (TEEMAH, SCREAM)}
    assert tops[TEEMAH] == tops[SCREAM] == 0x600000 + eb.blob(_w4(stock), 0)[0xf]


@pytest.mark.firmware
def test_scream_808_gets_the_four_knobs_after_its_own(built):
    stock, new, _ = built
    old, ps = _params(stock, SCREAM), _params(new, SCREAM)
    assert ps[:len(old)] == old
    assert [(p[0], p[2]) for p in ps[len(old):]] == [('Frequency', 4), ('Dry Level', 5), ('Wet Level', 6), ('Mode', 7)]
    assert [p[6] for p in ps[len(old):]] == [20.0, -60.0, 0.0, 0]
    assert ps[len(old) + 1][5] == pt.DRY_MAX


@pytest.mark.firmware
def test_scream_808_knobs_write_filters_mixer_and_types(built):
    import pmap
    _, new, _ = built
    fw = pdist.Fw(kp.get_entry_swapped(new, 3), _w4(new))
    r = pmap.model_report(fw, kp.find_model(fw.e3, SCREAM))
    writes = {(tuple(w['path']), w['pid']): pdist._knobs(w['expr'], set()) for w in r['writes']}
    assert {k for k, v in writes.items() if 'Frequency' in v} == {((s,), 2) for s in (2, 3, 4, 5)}
    assert {k for k, v in writes.items() if 'Dry Level' in v} == {((10,), 2)}
    assert {k for k, v in writes.items() if 'Wet Level' in v} == {((10,), 1)}
    assert {k for k, v in writes.items() if 'Mode' in v} == {((2,), 1), ((3,), 1)}
    assert {k for k, v in writes.items() if 'Level' in v} == {((11,), 1)}


@pytest.mark.firmware
def test_every_other_model_and_blob_is_stock(built):
    stock, new, _ = built
    e3o, e3n = (kp.parse_entry3_full(kp.get_entry_swapped(x, 3)) for x in (stock, new))
    view = lambda info: [(m['name'], m['meta'], [(p['name'], p['type'], p['slot'], p['meta'], p['min'], p['max'])
                                                 for p in m['params']])
                         for m in info['models'] if m['name'] not in (TEEMAH, SCREAM)]
    assert repr(view(e3o)) == repr(view(e3n))
    wo, wn = _w4(stock), _w4(new)
    changed = {_model(stock, n)['blob_index'] for n in (TEEMAH, SCREAM)}
    assert all(eb.blob(wo, i) == eb.blob(wn, i) for i in range(1, wo[2]) if i not in changed)
    bo, bn = eb.blob(wo, 0), eb.blob(wn, 0)
    assert bn[:len(bo)][0x11:] == bo[0x11:] and bn[0xf] == bo[0xf] + 1


@pytest.mark.firmware
def test_entry_3_grows_once_per_model(built):
    stock, new, d3 = built
    grow = len(kp.get_entry_swapped(new, 3)) - len(kp.get_entry_swapped(stock, 3))
    per = lambda n: 52 * (len(_model(stock, n)['params']) + pdist.N_KNOBS)
    assert grow <= per(TEEMAH) + per(SCREAM) + 64
    assert grow < len(kp.get_entry_swapped(d3, 3)) - len(kp.get_entry_swapped(stock, 3))


@pytest.mark.firmware
def test_g1_rule_rejects_a_model_with_a_member_after_level():
    with pytest.raises(SystemExit, match='not group G1'):
        fw = pdist.Fw(kp.get_entry_swapped(firmware.stock(), 3), _w4(firmware.stock()))
        pdist.plan_model(fw, kp.find_model(fw.e3, 'HD2_DistPrizeDriveMono'), group='g1')


# Rollout D3: every core mono distortion (docs/research/sharc/cores/teemah.md, Rollout D3)

CORE = (
    'HD2_DistMegaphoneMono', 'HD2_DistValveDriverMono', 'HD2_DistHedgehogD9Mono', 'HD2_DistIndustrialFuzzMono',
    'HD2_DistTriangleFuzzMono', 'HD2_DistMinotaurMono', 'HD2_DistCompulsiveDriveMono', 'HD2_DistTopSecretODMono',
    'HD2_DistVerminDistMono', 'HD2_DistArbitratorFuzzMono', 'HD2_DistTycoctaviaFuzzMono', 'HD2_DistScream808Mono',
    'HD2_DistBitcrusherMono', 'HD2_DistKWBMono', 'HD2_DistTeemahMono', 'HD2_DistWringerFuzzMono',
    'HD2_DistStuporODMono', 'HD2_DistObsidian7000Mono', 'HD2_DistClawthornDriveMono', 'HD2_DistThrifterFuzzMono',
    'HD2_DistKinkyBoostMono', 'HD2_DistDerangedMasterMono', 'HD2_DistDeezOneVintageMono', 'HD2_DistDeezOneModMono',
    'HD2_DistDhyanaDriveMono', 'HD2_DistAmpegScramblerODMono', 'HD2_DistZeroAmpBassDIMono',
    'HD2_DistToneSovereignMono', 'HD2_DistHeirApparentMono', 'HD2_DistXenomorphFuzzMono', 'HD2_DistAlpacaRougeMono',
    'HD2_DistLegendaryDriveMono', 'HD2_DistRamsHeadMono', 'HD2_DistSwedishChainsawMono', 'HD2_DistHorizonDriveMono',
    'HD2_DistBallisticFuzzMono', 'HD2_DistPocketFuzzMono', 'HD2_DistRatatouilleDistMono', 'HD2_DistVitalDistMono',
    'HD2_DistVitalBoostMono', 'HD2_DistPillarsMono', 'HD2_DistDarkDoveFuzzMono', 'HD2_DistPrizeDriveMono',
    'HD2_DistRegalBassDIMono')
CANDIDATES = CORE + pdist.LEGACY
PATCHED = tuple(n for n in CANDIDATES if n not in pdist.SKIP)
NAMES = {'freq': 'Frequency', 'dry': 'Dry Level', 'wet': 'Wet Level', 'mode': 'Mode'}


@pytest.fixture(scope='module')
def full():
    import pmap
    stock = firmware.stock()
    new = pdist.patch_dist(stock, log=lambda *_: None)
    fs, fn = (pdist.Fw(kp.get_entry_swapped(x, 3), _w4(x)) for x in (stock, new))
    plans = {n: pdist.plan_model(fs, kp.find_model(fs.e3, n)) for n in PATCHED}
    reports = {n: (pmap.model_report(fs, kp.find_model(fs.e3, n)), pmap.model_report(fn, kp.find_model(fn.e3, n)))
               for n in PATCHED}
    return stock, new, plans, reports


@pytest.mark.firmware
def test_the_core_mono_distortions_are_these_44():
    assert pdist.core_mono(kp.parse_entry3_full(firmware.stock_entry(3))) == CORE and len(CORE) == 44


@pytest.mark.firmware
def test_the_candidates_are_the_core_ones_and_the_17_legacy_ones():
    """Rollout D5: the unit's Distortion > Legacy list (Entry 0 browser list 10: fifteen HD2_DM4*, Bronze Master,
    Killer Z; helixhelp.com, Distortion: Legacy, 17 models), all mono; not Bass Octaver (pitch) nor Boost Comp and
    the other DM4 compressors (dynamics)."""
    info = kp.parse_entry3_full(firmware.stock_entry(3))
    assert pdist.candidates(info) == CANDIDATES and len(CANDIDATES) == 61 and len(pdist.LEGACY) == 17
    rest = [kp.find_model(info, n)['meta'] for n in CANDIDATES[len(CORE):]]
    assert all(m['subcategory'] == 'mono' for m in rest)
    assert [m['category'] for m in rest] == ['distortion'] * 17
    dm4 = [m for m in info['models'] if m['name'].startswith('HD2_DM4') and m['name'] not in CANDIDATES]
    assert sorted(m['meta']['category'] for m in dm4) == ['dynamics'] * (len(dm4) - 1) + ['pitch']


def test_a_model_without_a_static_model_table_row_is_left_out():
    """An HXtra build removes Xenomorph Fuzz Mono's Table 1 row for NAM A2 Lite; patch_dist then skips it."""
    info = kp.parse_entry3_full(firmware.stock_entry(3))
    rows = pdist.table1_names(firmware.stock_e0())
    assert pdist.core_mono(info, rows) == CORE, 'stock: every core mono distortion has its row'
    xeno = kp.find_model(info, 'HD2_DistXenomorphFuzzMono')['words'][2]
    assert pdist.core_mono(info, rows - {xeno}) == tuple(n for n in CORE if n != 'HD2_DistXenomorphFuzzMono')


@pytest.mark.firmware
def test_the_skipped_models_stay_stock(full):
    """Clawthorn Drive and Bitcrusher: on the unit their dry path carried the block's own output, with a tap and with
    a low-pass reading the in-port's buffer alike (2026-11-20); the dry path of Pocket Fuzz, KWB, Swedish Chainsaw and
    Alpaca Rouge followed the gain knob (clean, louder with gain, silent at 0; 2026-11-20); that of Ballistic Fuzz, Dark Dove
    Fuzz and Bighorn Fuzz (Ram's Head) was distorted and followed it, and Valve Driver stayed silent at Wet Level -60 dB
    whatever Dry Level and Gain did (2026-11-21). They keep their stock parameters and blob."""
    stock, new, _, _ = full
    assert set(pdist.SKIP) == {'HD2_DistClawthornDriveMono', 'HD2_DistBitcrusherMono',
                               'HD2_DistPocketFuzzMono', 'HD2_DistKWBMono', 'HD2_DistSwedishChainsawMono',
                               'HD2_DistAlpacaRougeMono', 'HD2_DistBallisticFuzzMono', 'HD2_DistDarkDoveFuzzMono',
                               'HD2_DistRamsHeadMono', 'HD2_DistValveDriverMono'} and len(PATCHED) == 51
    for n in pdist.SKIP:
        i = _model(stock, n)['blob_index']
        assert repr(_params(new, n)) == repr(_params(stock, n))
        assert eb.blob(_w4(new), i) == eb.blob(_w4(stock), i)


def _mode_disp(r_stock):
    """The new Mode knob's display name: "Crossover" where the model has a Mode of its own (Pillars)."""
    return pdist.MODE_DISP2[0] if 'Mode' in [q['name'] for q in r_stock['params']] else 'Mode'


def _writes(r, knob):
    return {(tuple(w['path']), w['pid']): w for w in r['writes'] if knob in pdist._knobs(w['expr'], set())}


def _type(r, path):
    return next(c['type'] for c in r['chain'] if tuple(c['path']) == path)


@pytest.mark.firmware
@pytest.mark.parametrize('name', PATCHED)
def test_each_model_gets_its_knobs_after_its_own(full, name):
    stock, new, plans, _ = full
    knobs = plans[name]['knobs']
    old, ps = _params(stock, name), _params(new, name)
    at = pdist.insert_at(_model(stock, name)['params'])             # before a hidden one (Bronze Master's Smart)
    assert ps[:at] == old[:at] and ps[at + len(knobs):] == old[at:] and len(ps) == len(old) + len(knobs)
    mine = ps[at: at + len(knobs)]
    mode = 'Xover' if 'Mode' in [p[0] for p in old] else 'Mode'
    assert [p[0] for p in mine] == [NAMES[k] for k in knobs[:-1]] + [mode]
    assert [p[2] for p in mine] == list(range(old[at - 1][2] + 1, old[at - 1][2] + 1 + len(knobs)))
    assert [p[6] for p in mine] == [{'freq': 20.0, 'dry': -60.0, 'wet': 0.0, 'mode': 0}[k] for k in knobs]


@pytest.mark.firmware
@pytest.mark.parametrize('name', PATCHED)
def test_each_models_knobs_write_crossover_and_mixer(full, name):
    import pmap
    _, _, plans, reports = full
    p, (ro, rn) = plans[name], reports[name]
    freq, mode = _writes(rn, 'Frequency'), _writes(rn, _mode_disp(ro))
    assert len(freq) == 4 and {pid for _, pid in freq} == {2} and all(_type(rn, k[0]) == '0x102' for k in freq)
    assert len(mode) == 2 and {pid for _, pid in mode} == {1} and {k[0] for k in mode} < {k[0] for k in freq}
    dry, wet = _writes(rn, 'Dry Level'), _writes(rn, 'Wet Level')
    if p['group'] == 'xover':
        assert not dry and not wet
        return
    (dpath, dpid), = dry
    (wpath, wpid), = wet
    assert dpath == wpath and (dpid, wpid) == (2, 1) and _type(rn, dpath) == '0x84'
    top = [c for c in rn['chain'] if c['depth'] == 0]
    if p['group'] in ('g3', 'g4'):                       # the mixer feeds the out-port
        assert dpath[0] == top[-1]['path'][0] - 1
    else:                                                # the mixer feeds the member Level writes first
        lname = pdist.GROUPS.get(name, (None, None))[1]
        (lv,) = [q for q in ro['params'] if (q['name'] == lname if lname else q['name'] in pdist.LEVEL_KNOBS)]
        assert dpath[0] == min(k[0][0] for k in _writes(rn, lv['disp']) if k[0][0] != dpath[0]) - 1
    e = dry[(dpath, dpid)]['expr']
    if p['group'] == 'g3':                               # Level reaches the dry signal, 0 dB at its default
        knobs = {q['slot']: q['default'] for q in rn['params']}
        assert pmap.evaluate(e, knobs) == pytest.approx(-60.0, abs=1e-4)
        knobs[p['level'][0]] = 1.0
        top_db = p['level'][1][-1]
        assert pmap.evaluate(e, knobs) == pytest.approx(-60.0 + top_db, abs=1e-3) and top_db > 0
    else:
        assert pdist._knobs(e, set()) == {'Dry Level'}


@pytest.mark.firmware
@pytest.mark.parametrize('name', PATCHED)
def test_each_models_stock_writes_survive_renumbered(full, name):
    _, _, plans, reports = full
    ro, rn = reports[name]
    p = plans[name]
    shift = (lambda s: s if s <= p['at'] else s + 4) if p['group'] == 'xover' else \
        (lambda s: s if s <= 1 else s + 4 if s < p['at'] else s + 5)          # path [0]: the root itself
    stock = sorted((tuple([shift(w['path'][0])] + w['path'][1:]), w['pid'], repr(w['expr'])) for w in ro['writes'])
    mine = {'Frequency', 'Dry Level', 'Wet Level', _mode_disp(ro)}
    new = sorted((tuple(w['path']), w['pid'], repr(w['expr'])) for w in rn['writes']
                 if not pdist._knobs(w['expr'], set()) & mine)
    assert new == stock


@pytest.mark.firmware
@pytest.mark.parametrize('name', PATCHED)
def test_each_models_composite_keeps_its_connections(full, name):
    """Stock members keep their connection words (record words 1/2: [2, offset, 0] then [id, 0], the offset pointing
    right after), the header keeps word 0; new members have none. A build that dropped them hung the unit on Alpaca
    Rouge (2026-11-20): word 1 stayed 3 while word 2 pointed at the next member's data."""
    stock, new, plans, reports = full
    p = plans[name]
    shift = (lambda s: s if s <= p['at'] else s + 4) if p['group'] == 'xover' else \
        (lambda s: s if s <= 1 else s + 4 if s < p['at'] else s + 5)
    bo, bn = eb.blob(_w4(stock), 0), eb.blob(_w4(new), 0)
    ho, old = pdist._composite(bo, p['root'])
    hn, mem = pdist._composite(bn, int(reports[name][1]['root'], 16))      # asserts the form of every connection
    assert hn[0] == ho[0]
    assert [mem[shift(k + 1) - 1][4] for k in range(len(old))] == [o[4] for o in old]
    kept = {shift(k + 1) - 1 for k in range(len(old))}
    assert all(m[4] is None and m[0][1] == 0 for k, m in enumerate(mem) if k not in kept)
    assert all(m[0][4] != 1 for k, m in enumerate(mem) if k not in kept), 'a new copy member (tap)'


@pytest.mark.firmware
@pytest.mark.parametrize('name', PATCHED)
def test_each_models_composite_keeps_its_list(full, name):
    """Header words 4/5 (count and offset of the composite's list, 4 words an entry) are the stock root's, and no
    member names (record word 19) an entry past the count. A build that wrote 0 left Megaphone's member 0x2396 naming
    entry 1 of an empty list and hung the unit after a run of model changes (2026-11-21)."""
    stock, new, plans, reports = full
    bo, bn = eb.blob(_w4(stock), 0), eb.blob(_w4(new), 0)
    ho, _ = pdist._composite(bo, plans[name]['root'])
    hn, mem = pdist._composite(bn, int(reports[name][1]['root'], 16))
    assert hn[4] == ho[4] and (hn[5] == ho[5] or not ho[4])
    assert max(m[0][19] for m in mem) <= hn[4]


@pytest.mark.firmware
def test_no_member_names_a_list_entry_its_composite_lacks(full):
    """Every composite of the new Blob 0, stock and new: record word 19 of each member at most header word 4."""
    _, new, _, _ = full
    b0 = eb.blob(_w4(new), 0)
    ct = b0[0x10]
    bad = []
    for i in range(b0[0xf]):
        h = b0[ct + 7 * i: ct + 7 * i + 7]
        refs = max((b0[h[3] + 20 * k + 19] for k in range(h[2])), default=0)
        if refs > h[4]:
            bad.append((hex(0x600000 + i), refs, h[4]))
    assert not bad


def _row(l6, name_idx):
    """Entry 0 UI Model Info Table counts (shown, saved in a preset, all) of a model."""
    e0 = kp.get_entry_swapped(l6, 0)
    segs = kp._parse_e0_segments(e0)
    A = kp.e0_device(segs)
    fo = lambda va, ln=4: kp._e0_vaddr_to_foff(segs, va, ln)
    tab = struct.unpack_from('<I', e0, fo(A.TAB2_BASE_PTR))[0]
    _, cnt = kp._decode_thumb_movw(e0[fo(A.TAB2_CNT_INSN): fo(A.TAB2_CNT_INSN) + 4])
    rows = [tab + 44 * i for i in range(cnt) if struct.unpack_from('<I', e0, fo(tab + 44 * i))[0] == name_idx]
    assert len(rows) == 1
    return struct.unpack_from('<3I', e0, fo(rows[0] + 24, 12))


@pytest.mark.firmware
@pytest.mark.parametrize('name', PATCHED)
def test_each_models_new_knobs_are_shown_and_saved(full, name):
    """Entry 0's row and the Entry 3 meta displayParamCount / presetParamCount (legacy models and Bronze Master have
    them) count the new knobs; hidden ones stay hidden (Bronze Master's Smart: 4, 4, 5 -> 8, 8, 9)."""
    stock, new, plans, _ = full
    k = len(plans[name]['knobs'])
    mo, mn = _model(stock, name), _model(new, name)
    assert _row(new, mn['words'][2]) == tuple(c + k for c in _row(stock, mo['words'][2]))
    for key in pdist.SHOWN_META:
        assert (key in mn['meta']) == (key in mo['meta'])
        if key in mo['meta']:
            assert int(mn['meta'][key]) == int(mo['meta'][key]) + k
    assert {x: v for x, v in mn['meta'].items() if x not in pdist.SHOWN_META} == \
        {x: v for x, v in mo['meta'].items() if x not in pdist.SHOWN_META}


@pytest.mark.firmware
@pytest.mark.parametrize('name', (pdist.BRONZE, pdist.KILLER_Z))
def test_knobs_go_before_a_hidden_smart(full, name):
    """Bronze Master and Killer Z (one root): Smart (slot 10000, no state, writes nothing) stays last and hidden; the
    slot map (table D: node, index in Entry 3's parameter list, slot) keeps slot order and moves Smart's index up by
    four; Entry 0's row grows by four in each count (Bronze Master 4, 4, 5 -> 8, 8, 9)."""
    stock, new, _, _ = full
    old = _params(stock, name)
    assert old[-1][0] == 'Smart' and old[-1][2] == pdist.HIDDEN_SLOT and pdist.insert_at(_model(stock, name)['params']) == len(old) - 1
    slots = [p[2] for p in old[:-1]]
    want = slots + list(range(slots[-1] + 1, slots[-1] + 5)) + [pdist.HIDDEN_SLOT]
    assert [p[0] for p in _params(new, name)] == [p[0] for p in old[:-1]] + ['Frequency', 'Dry Level', 'Wet Level',
                                                                             'Mode', 'Smart']
    assert [p[2] for p in _params(new, name)] == want
    ms, m = _model(stock, name), _model(new, name)
    assert _row(stock, ms['words'][2]) == (len(old) - 1, len(old) - 1, len(old))
    assert ms['words'][6] == len(old) - 1 and m['words'][6] == len(old) + 3        # no state for Smart
    pm = pt.Blob(eb.blob(_w4(new), m['blob_index'])).pm
    d = [pm[pm[6] + 4 * k: pm[6] + 4 * k + 4] for k in range(pm[21])]
    assert [(r[1], r[2]) for r in d] == list(enumerate(want)) and pm[22] == len(old) + 3


@pytest.mark.firmware
def test_full_build_keeps_every_other_model_and_blob_stock(full):
    stock, new, plans, _ = full
    e3o, e3n = (kp.parse_entry3_full(kp.get_entry_swapped(x, 3)) for x in (stock, new))
    view = lambda info: [(m['name'], m['meta'], [(p['name'], p['type'], p['slot'], p['meta'], p['min'], p['max'])
                                                 for p in m['params']])
                         for m in info['models'] if m['name'] not in PATCHED]
    assert repr(view(e3o)) == repr(view(e3n))
    wo, wn = _w4(stock), _w4(new)
    changed = {_model(stock, n)['blob_index'] for n in PATCHED}
    assert all(eb.blob(wo, i) == eb.blob(wn, i) for i in range(1, wo[2]) if i not in changed)
    bo, bn = eb.blob(wo, 0), eb.blob(wn, 0)
    roots = {p['root'] for p in plans.values()}
    assert bn[:len(bo)][0x11:] == bo[0x11:] and bn[0xf] == bo[0xf] + len(roots) == bo[0xf] + 35


@pytest.mark.firmware
def test_full_build_fits_with_room_to_spare(full):
    stock, new, plans, _ = full
    b0 = eb.blob(_w4(new), 0)
    assert kp.sharc_blob0_check(list(b0), kp.SHARC_BLOB0_MAX_WORDS) is None
    assert len(b0) - len(eb.blob(_w4(stock), 0)) < 33000                 # plan: about 33 000 words
    grow = len(kp.get_entry_swapped(new, 3)) - len(kp.get_entry_swapped(stock, 3))
    meta = sum(12 * len(_model(stock, n)['meta_recs']) for n in PATCHED        # e3_show_all's copies
               if set(pdist.SHOWN_META) & set(_model(stock, n)['meta']))
    assert grow <= sum(52 * (len(_model(stock, n)['params']) + len(plans[n]['knobs'])) for n in PATCHED) + meta + 2 * 64
    assert len(kp.get_entry_swapped(new, 4)) <= kp.E4_MAX
    for n in PATCHED:
        assert not kp.pmap_slots_without_param(kp.get_entry_swapped(new, 4), _model(new, n)['blob_index'],
                                               _model(new, n)['params']), n


# e4_write on synthetic Entry 4 images: the two layouts, stock and HXtra's (directory relocated)

def _e4(blobs, relocated):
    """An Entry 4 of len(blobs) blobs: stock (directory at 6, Blob 0 first) or HXtra (a relocated directory, then
    Blob 0, then the last blob, as an earlier step leaves them)."""
    n = len(blobs)
    w = [0x28267e5e, 4, n, 6, 0, 0] + [0] * (2 * n)
    pairs = [None] * n
    order = list(range(n)) if not relocated else list(range(1, n - 1))
    for k in order:
        pairs[k] = (len(blobs[k]), len(w)); w += blobs[k]
    if relocated:
        dead = len(w); w += [0xdead] * 4                   # a stock Blob 0 left behind
        d = len(w); w += [0] * (2 * n)
        for k in (0, n - 1):
            pairs[k] = (len(blobs[k]), len(w)); w += blobs[k]
        w[3] = d
        assert dead
    d = w[3]
    for k, (ln, o) in enumerate(pairs):
        w[d + 2 * k: d + 2 * k + 2] = [ln, o]
    w[4], w[5] = pairs[0][1], len(w)
    return struct.pack('<%dI' % len(w), *w)


def _blobs_of(e4):
    w = struct.unpack('<%dI' % (len(e4) // 4), e4)
    assert w[5] == len(w) and w[4] == w[w[3] + 1]
    return [list(eb.blob(w, k)) for k in range(w[2])], w


@pytest.mark.parametrize('relocated', [False, True], ids=['stock', 'hxtra'])
def test_e4_write_replaces_blob_0_and_grown_blobs_and_keeps_the_rest(relocated):
    import e4_compact
    blobs = [[100 + k] * (3 + k) for k in range(6)]
    e4 = _e4(blobs, relocated)
    new0, new2, new5 = [7] * 9, [8] * 20, [9] * 8
    e = pdist.e4_write(e4, new0, {2: new2, 5: new5})
    out, w = _blobs_of(e)
    assert out == [new0, blobs[1], new2, blobs[3], blobs[4], new5]
    assert e4_compact.compact(e) == e, 'written without gaps: no dead copy left behind'
    assert len(w) == 6 + 2 * len(blobs) + sum(map(len, out))


def test_e4_write_rewrites_a_blob_of_the_same_length():
    blobs = [[100 + k] * 4 for k in range(4)]
    out, _ = _blobs_of(pdist.e4_write(_e4(blobs, False), blobs[0], {2: [5] * 4}))
    assert out == [blobs[0], blobs[1], [5] * 4, blobs[3]]


# ---- tools/e4_compact.py: Entry 4 without dead copies, the last step on it

@pytest.mark.parametrize('relocated', [False, True], ids=['stock', 'hxtra'])
def test_compact_keeps_every_blob_and_drops_every_gap(relocated):
    import e4_compact
    blobs = [[100 + k] * (3 + k) for k in range(6)]
    e4 = pdist.e4_write(_e4(blobs, relocated), [7] * 9, {2: [8] * 20})
    before, _ = _blobs_of(e4)
    c = e4_compact.compact(e4)
    after, w = _blobs_of(c)
    assert after == before
    n = len(blobs)
    assert w[:4] == (0x28267e5e, 4, n, 6) and w[4] == 6 + 2 * n
    assert len(w) == 6 + 2 * n + sum(map(len, before))
    offs = [w[6 + 2 * k + 1] for k in range(n)]
    assert offs == [6 + 2 * n + sum(map(len, before[:k])) for k in range(n)]     # index order, no gap
    assert e4_compact.compact(c) == c


def test_compact_leaves_a_compact_entry_4_as_it_is():
    import e4_compact
    e4 = _e4([[100 + k] * (3 + k) for k in range(5)], False)
    assert e4_compact.compact(e4) == e4


@pytest.mark.firmware
def test_compact_keeps_stock_and_patch_dist_writes_compact(full):
    import e4_compact
    stock, new, _, _ = full
    s4 = kp.get_entry_swapped(stock, 4)
    assert e4_compact.compact(s4) == s4
    n4 = kp.get_entry_swapped(new, 4)
    assert e4_compact.compact(n4) == n4, 'patch_dist leaves no dead copy'


def test_build_compacts_entry_4_last_with_or_without_features(tmp_path, monkeypatch):
    from test_gate import build_steps
    for env in ({}, {k: '1' for k in ('HX_CLICK', 'HX_FX_GATE', 'HX_DIST_XOVER', 'HX_GR')}):
        for k in ('HX_DIST_XOVER', 'HX_GATE_SC', 'HX_USB_MONITOR'):
            monkeypatch.delenv(k, raising=False)
        ran = build_steps(tmp_path, monkeypatch, 'HX_Stomp', env)
        assert ran[-3:] == ['tools/e4_compact.py', 'tools/l6ia_verify.py', 'tools/hxf.py'], env


def test_web_worker_compacts_where_build_py_does():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / 'web/firmware-worker.js').read_text()
    at = lambda s: js.index(s, js.index('step(5,'))
    assert at('"tools/patch_comp.py", "build/') < at('"tools/e4_compact.py", "build/') < at('"tools/l6ia_verify.py", "build/')
    assert '"tools/e4_compact.py",\n' in js


# ---- build.py, the web page and the feature switch

def test_build_runs_it_after_every_step_that_adds_a_model_and_before_the_gr_knobs(tmp_path, monkeypatch):
    from test_gate import build_steps
    for k in ('HX_DIST_XOVER', 'HX_GATE_SC', 'HX_USB_MONITOR'):
        monkeypatch.delenv(k, raising=False)
    flags = ('HX_CLICK', 'HX_FX_GATE', 'HX_GATE_SC', 'HX_USB_MONITOR', 'HX_DIST_XOVER', 'HX_GR')
    ran = build_steps(tmp_path, monkeypatch, 'HX_Stomp', {k: '1' for k in flags})
    adders = ('tools/patch_click.py', 'tools/patch_gate.py', 'tools/patch_sidechain.py', 'tools/patch_usbmon.py')
    assert max(ran.index(s) for s in adders) < ran.index('tools/patch_dist.py') < ran.index('tools/patch_comp.py')


def test_build_without_the_flag_has_no_crossover(tmp_path, monkeypatch):
    from test_gate import build_steps
    monkeypatch.delenv('HX_DIST_XOVER', raising=False)
    assert 'tools/patch_dist.py' not in build_steps(tmp_path, monkeypatch, 'HX_Stomp', {})


def test_web_worker_runs_it_where_build_py_does():
    # the same order as build.py: after the USB monitor (the last step that adds a model), before the GR knobs
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / 'web/firmware-worker.js').read_text()
    at = lambda s: js.index(s, js.index('step(5,'))
    assert at('"tools/patch_usbmon.py", "build/') < at('"tools/patch_dist.py", "build/') < at('"tools/patch_comp.py", "build/')
    assert 'flags.indexOf("HX_DIST_XOVER")' in js
    for f in ('tools/patch_dist.py', 'tools/features/dist_xover.py', 'tools/patch_teemah.py', 'tools/sharc/pmap.py'):
        assert f'"{f}"' in js


def test_off_unless_asked_for_and_a_box_on_the_page():
    import features
    import m4cache
    from features import dist_xover
    mods = features.load()
    assert dist_xover.FLAG == 'HX_DIST_XOVER'
    assert dist_xover.FLAG in m4cache.web_options() and dist_xover.FLAG not in m4cache.web_flags() + m4cache.web_dsp()
    assert dist_xover not in features.enabled(mods, {})
    assert dist_xover in features.enabled(mods, {'HX_DIST_XOVER': '1'})
    assert not hasattr(dist_xover, 'apply') and not hasattr(dist_xover, 'SETTINGS')


# ---- tools/space_report.py: the space each limited part of the finished image uses

@pytest.mark.firmware
def test_space_report_reads_blob_0_entry_3_and_entry_4():
    import space_report
    stock = firmware.stock()
    rows = {name: (used, limit) for name, used, limit, _ in space_report.rows(stock, 0xf10f0)}
    assert rows['Blob 0'] == (490_156, kp.SHARC_BLOB0_MAX_WORDS)
    assert rows['Entry 3'] == (len(kp.get_entry_swapped(stock, 3)), 0xf10f0)
    assert rows['Entry 4'] == (len(kp.get_entry_swapped(stock, 4)), kp.E4_MAX)
    text = space_report.report(stock, 0xf10f0)
    assert text.count('[=] ') == 3 and 'Blob 0: 490,156 of 585,216 words' in text


def test_build_and_worker_report_the_space_after_the_last_compaction():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    assert 'run(py, "tools/e4_compact.py", image, titled, "--report", hex(E3_MAX))' in (root / 'build.py').read_text()
    js = (root / 'web/firmware-worker.js').read_text()
    assert '"--report", "0x" + E3_MAX.toString(16)' in js and '"tools/space_report.py",\n' in js

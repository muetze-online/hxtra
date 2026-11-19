"""tools/patch_teemah.py: Teemah! on a copy of its composite with a crossover and a dry mix (test build A)."""
import struct

import pytest

import entry4_blob as eb
import firmware
import kit_patch_amp as kp
import patch_teemah as pt

pytestmark = pytest.mark.firmware


def _blob0_and_teemah(l6ia_bytes):
    e4 = kp.get_entry_swapped(l6ia_bytes, 4)
    w4 = struct.unpack('<%dI' % (len(e4) // 4), e4)
    m = kp.find_model(kp.parse_entry3_full(kp.get_entry_swapped(l6ia_bytes, 3)), pt.MODEL)
    return list(eb.blob(w4, 0)), list(eb.blob(w4, m['blob_index']))


def _members(b0, comp):
    ct = b0[0x10]
    h = b0[ct + 7 * (comp - 0x600000): ct + 7 * (comp - 0x600000) + 7]
    out = []
    for m in range(h[2]):
        r = b0[h[3] + 20 * m: h[3] + 20 * m + 20]
        out.append((r[6], r[4], b0[r[8]: r[8] + r[7]], b0[r[10]: r[10] + r[9]],
                    dict((b0[r[12] + 2 * k], b0[r[12] + 2 * k + 1]) for k in range(r[11]))))
    return h, out


@pytest.fixture(scope='module')
def built():
    stock = firmware.stock()
    return stock, pt.patch_teemah(stock, freq=250.0, dry_db=-6.0, log=lambda *_: None)


def test_the_new_composite_taps_filters_and_mixes_before_level(built):
    b0, blob = _blob0_and_teemah(built[1])
    comp = 0x600000 + b0[0xf] - 1
    h, ms = _members(b0, comp)
    assert h[1] == 2 and h[2] == 13
    assert [t for t, *_ in ms] == [0, 0, 0x102, 0x102, 0x102, 0x6000c2, 0x102, 0x102, 0x102, 0x102, 0x84, 0x80, 0]
    assert ms[1][1:4] == (1, [0], [1])                       # the tap: buffer 0 copied into 1
    assert ms[2][4][1] == ms[3][4][1] == 4                   # LR4: two 2nd-order high-passes (filters.md)
    assert ms[8][2:4] == ([1], [1]) and ms[8][4][1] == ms[9][4][1] == 3
    assert ms[2][4][4] == pt.f2u(0.7071)
    assert ms[2][4][2] == pt.f2u(250.0)
    assert ms[10][2:4] == ([0, 1], [0]) and ms[10][4] == {1: pt.f2u(0.0), 2: pt.f2u(-6.0)}
    assert kp.sharc_blob0_check(b0, kp.SHARC_BLOB0_MAX_WORDS) is None


def test_the_model_blob_points_at_it_with_its_paths_moved(built):
    _, blob_old = _blob0_and_teemah(built[0])
    b0, blob = _blob0_and_teemah(built[1])
    old, new = pt.Blob(blob_old), pt.Blob(blob)
    assert new.root[1] == 0x600000 + b0[0xf] - 1
    assert [c[0] for c in new.root[2]] == [pt.SLOT_MAP[c[0]] for c in old.root[2]]
    moved = lambda p: (pt.SLOT_MAP[p[0]],) + p[1:] if p and p[0] in pt.SLOT_MAP else p
    assert [(moved(old.path(r[0])), r[1]) for r in old.pins] == [(new.path(r[0]), r[1]) for r in new.pins]
    assert sorted(moved(old.path(old.pm[d])) for d in old.writer_data()) == \
        sorted(new.path(new.pm[d]) for d in new.writer_data())


def test_sub_bindings_keep_their_node_tree_positions(built):
    """Sub-binding = {member path, node-tree position}; the first build renumbered the positions too (shared path
    words), which put Level, Treble and two filters on positions the tree does not have."""
    _, blob_old = _blob0_and_teemah(built[0])
    _, blob = _blob0_and_teemah(built[1])
    old, new = pt.Blob(blob_old), pt.Blob(blob)
    assert new.sub_map() == new.positions()
    assert sorted(new.sub_map().values()) == sorted(old.sub_map().values())


def test_blob_round_trips_every_stock_blob():
    stock = firmware.stock()
    e4 = kp.get_entry_swapped(stock, 4)
    w4 = struct.unpack('<%dI' % (len(e4) // 4), e4)
    n = 0
    for m in kp.parse_entry3_full(kp.get_entry_swapped(stock, 3))['models']:
        b = list(eb.blob(w4, m['blob_index']))
        if len(b) < 12 or b[0] != 3:
            continue
        blob = pt.Blob(b)
        assert blob.assemble() == b, m['name']
        assert blob.sub_map() == blob.positions(), m['name']
        n += 1
    assert n == 863                                        # every model but NO_ASSET


def test_stock_composites_are_untouched(built):
    b0_old, _ = _blob0_and_teemah(built[0])
    b0, _ = _blob0_and_teemah(built[1])
    assert b0[0xf] == b0_old[0xf] + 1
    assert _members(b0, pt.STOCK_COMP) == _members(b0_old, pt.STOCK_COMP)


def test_split_bypasses_the_low_passes():
    b0, _ = _blob0_and_teemah(pt.patch_teemah(firmware.stock(), split=True, log=lambda *_: None))
    _, ms = _members(b0, 0x600000 + b0[0xf] - 1)
    assert ms[8][4][1] == 0 and ms[9][4][1] == 0


def test_first_order_uses_one_section_a_side():
    b0, _ = _blob0_and_teemah(pt.patch_teemah(firmware.stock(), hp_type=2, lp_type=1, sections=1, log=lambda *_: None))
    _, ms = _members(b0, 0x600000 + b0[0xf] - 1)
    assert [ms[k][4][1] for k in (2, 3, 8, 9)] == [2, 0, 1, 0]


@pytest.fixture(scope='module', params=[True, False], ids=['bound', 'unbound'])
def knob(request):
    stock = firmware.stock()
    return stock, pt.patch_teemah(stock, freq=400.0, freq_knob=True, bind=request.param, log=lambda *_: None), \
        request.param


def _writers(blob):
    """{(path, pid): [knob slots feeding it]} of the top-level map's writers fed straight by an input node."""
    pm = blob.pm
    a, c, d = pm[0], pm[4], pm[6]
    nodes = [pm[a + 10 * k: a + 10 * k + 10] for k in range(pm[18])]
    slot = {r[0]: r[2] for r in (pm[d + 4 * k: d + 4 * k + 4] for k in range(pm[21]))}
    out = {}
    for r in (pm[c + 4 * k: c + 4 * k + 4] for k in range(pm[20])):
        n = nodes[r[1]]
        if n[3] == 1 and r[0] in slot:
            out.setdefault((blob.path(pm[n[0]]), pm[n[0] + 1]), []).append(slot[r[0]])
    return out


def test_freq_knob_is_slot_6_from_20_to_1000_hz(knob):
    m = kp.find_model(kp.parse_entry3_full(kp.get_entry_swapped(knob[1], 3)), pt.MODEL)
    p = m['params'][-1]
    assert [q['slot'] for q in m['params']] == [1, 2, 3, 4, 5, 6] and m['words'][6:8] == [6, 6]
    assert (p['type'], p['min'], p['max'], p['default']) == (3, 20.0, 1000.0, 400.0)
    assert p['meta']['displayType'] == 'hz'
    e4 = kp.get_entry_swapped(knob[1], 4)
    assert not kp.pmap_slots_without_param(e4, m['blob_index'], m['params'])


def test_freq_knob_writes_the_four_crossover_filters(knob):
    b0, blob = _blob0_and_teemah(knob[1])
    blob = pt.Blob(blob)
    w = _writers(blob)
    assert {k: v for k, v in w.items() if v == [6]} == {((3,), 2): [6], ((4,), 2): [6], ((9,), 2): [6],
                                                        ((10,), 2): [6]}
    _, ms = _members(b0, 0x600000 + b0[0xf] - 1)
    assert [(ms[s - 1][0], ms[s - 1][4][1]) for s in (3, 4, 9, 10)] == [(0x102, 4), (0x102, 4), (0x102, 3), (0x102, 3)]
    root = {blob.path(r[0]) for r in blob.pins if blob.path(r[0]) == (0,)}
    assert [r for r in blob.pins if blob.path(r[0]) == (0,) and r[1] == 6] and len(root) == 1
    dflt = [r for r in blob.pins if blob.path(r[0]) == (0,) and r[1] == 6][0]
    assert blob.area[dflt[2]: dflt[2] + 2] == [1, pt.f2u(400.0)]


def test_bound_filters_are_registered_as_stock_members_are(knob):
    _, blob = _blob0_and_teemah(knob[1])
    blob = pt.Blob(blob)
    kids = [c[0] for c in blob.root[2]]
    assert blob.sub_map() == blob.positions()
    if knob[2]:
        assert kids == [6, 12, 5, 7, 8, 3, 4, 9, 10]
        assert blob.pm[blob.pm[10]: blob.pm[10] + 8] != [0] * 8 and blob.pm[23] == 9
        assert {(blob.path(r[0]), r[1]) for r in blob.pins if blob.path(r[0]) in {(3,), (4,), (9,), (10,)}} == \
            {((s,), p) for s in (3, 4, 9, 10) for p in (1, 2, 3, 4)}
    else:
        assert kids == [6, 12, 5, 7, 8] and blob.pm[23] == 5


def test_freq_knob_leaves_every_other_model_alone(knob):
    stock, new, _ = knob
    e3o, e3n = (kp.parse_entry3_full(kp.get_entry_swapped(x, 3)) for x in (stock, new))
    view = lambda info: [(m['name'], m['meta'], [(p['name'], p['type'], p['slot'], p['meta'], p['min'], p['max'])
                                                 for p in m['params']])
                         for m in info['models'] if m['name'] != pt.MODEL]
    assert repr(view(e3o)) == repr(view(e3n))                 # repr: int words read as floats can be NaN
    wo, wn = (struct.unpack('<%dI' % (len(e) // 4), e) for e in
              (kp.get_entry_swapped(x, 4) for x in (stock, new)))
    teemah = kp.find_model(e3o, pt.MODEL)['blob_index']
    assert all(eb.blob(wo, i) == eb.blob(wn, i) for i in range(1, wo[2]) if i != teemah)


def test_without_freq_knob_entry_3_is_stock(built):
    assert kp.get_entry_swapped(built[0], 3) == kp.get_entry_swapped(built[1], 3)


# Test build C (--knobs): Freq, Mix, Mode

@pytest.fixture(scope='module', params=[(1.0, 1), (0.3, 0)], ids=['crossover', 'full'])
def knobs(request):
    stock = firmware.stock()
    mix, mode = request.param
    return stock, pt.patch_teemah_c(stock, freq=400.0, mix=mix, mode=mode, log=lambda *_: None), mix, mode


def _feeds(blob):
    """{(member path, pid): (knob slot, how)} of the top-level map: how None (the knob's value), ('curve', 9 values)
    or ('select', 2 data words), one node between knob and writer at most."""
    pm = blob.pm
    a, b, c, d = pm[0], pm[2], pm[4], pm[6]
    nodes = [pm[a + 10 * k: a + 10 * k + 10] for k in range(pm[18])]
    slot = {r[0]: r[2] for r in (pm[d + 4 * k: d + 4 * k + 4] for k in range(pm[21]))}
    tables = {r[2]: [struct.unpack('<f', struct.pack('<I', w))[0] for w in pm[r[0]: r[0] + r[4]]]
              for r in (pm[b + 6 * k: b + 6 * k + 6] for k in range(pm[19]))}
    into = {}
    for r in (pm[c + 4 * k: c + 4 * k + 4] for k in range(pm[20])):
        into.setdefault(r[1], []).append(r[0])
    out = {}
    for i, n in enumerate(nodes):
        if n[3] != 1 or len(into.get(i, [])) != 1:
            continue
        src = into[i][0]
        if src in slot:
            how, knob = None, slot[src]
        elif len(into.get(src, [])) == 1 and into[src][0] in slot:
            knob = slot[into[src][0]]
            s = nodes[src]
            how = ('curve', tables[src]) if s[3] == 0x3 else ('select', pm[s[0]: s[0] + s[6]]) if s[3] == 0x2a else s[3]
        else:
            continue
        out[(blob.path(pm[n[0]]), pm[n[0] + 1])] = (knob, how)
    return out


def test_c_composite_has_three_buffers_and_two_mixers_before_level(knobs):
    _, new, mix, mode = knobs
    b0, _ = _blob0_and_teemah(new)
    h, ms = _members(b0, 0x600000 + b0[0xf] - 1)
    assert h[1] == 3 and h[2] == 17
    assert [t for t, *_ in ms] == [0, 0, 0, 0x102, 0x102, 0x102, 0x6000c2, 0x102, 0x102, 0x102, 0x102, 0x102, 0x102,
                                   0x84, 0x84, 0x80, 0]
    assert ms[1][1:4] == (1, [0], [1]) and ms[2][1:4] == (1, [0], [2])          # taps: low band, dry path
    hp, lp, hpd = (pt.C_HP, pt.C_LP, pt.C_HPD)
    assert [ms[s - 1][2:4] for s in hp + lp + hpd] == [([0], [0])] * 2 + [([1], [1])] * 2 + [([2], [2])] * 2
    assert [ms[s - 1][4][1] for s in hp + lp + hpd] == [4, 4, 3, 3] + [4 if mode else 0] * 2
    assert all(ms[s - 1][4][2] == pt.f2u(400.0) for s in hp + lp + hpd)
    assert ms[pt.C_MIX - 1][2:4] == ([0, 2], [0]) and ms[pt.C_LPMIX - 1][2:4] == ([0, 1], [0])
    assert ms[pt.C_MIX - 1][4] == {1: pt.f2u(pt.curve_at(pt.LINE6_WET, mix)), 2: pt.f2u(pt.curve_at(pt.LINE6_DRY, mix))}
    assert ms[pt.C_LPMIX - 1][4] == {1: pt.f2u(0.0), 2: pt.f2u(0.0 if mode else pt.OFF_DB)}
    assert kp.sharc_blob0_check(b0, kp.SHARC_BLOB0_MAX_WORDS) is None


def test_c_knobs_are_slots_6_to_8(knobs):
    _, new, mix, mode = knobs
    m = kp.find_model(kp.parse_entry3_full(kp.get_entry_swapped(new, 3)), pt.MODEL)
    assert [q['slot'] for q in m['params']] == list(range(1, 9)) and m['words'][6:8] == [8, 8]
    f, x, md = m['params'][5:]
    assert (f['name'], f['type'], f['min'], f['max'], f['meta']['displayType']) == ('Frequency', 3, 20.0, 1000.0, 'hz')
    assert (x['name'], x['type'], x['min'], x['max']) == ('Mix', 3, 0.0, 1.0) and x['default'] == pytest.approx(mix)
    assert (md['name'], md['type']) == ('Mode', 2)
    assert {k: v for k, v in md['meta'].items() if k.isdigit()} == {'0': 'Full', '1': 'Crossover'}
    rec = struct.unpack_from('<8i', kp.get_entry_swapped(new, 3), m['words'][11] + 32 * 7)
    assert rec[3:] == (2, 8, 0, 1, mode)
    assert not kp.pmap_slots_without_param(kp.get_entry_swapped(new, 4), m['blob_index'], m['params'])


def test_c_entry_3_strings_are_stock(knobs):
    import patch_comp
    assert patch_comp.e3_strings(kp.get_entry_swapped(knobs[0], 3))[1:] == \
        patch_comp.e3_strings(kp.get_entry_swapped(knobs[1], 3))[1:]


def test_c_knobs_write_filters_mixers_and_types(knobs):
    _, new, mix, mode = knobs
    _, blob = _blob0_and_teemah(new)
    blob = pt.Blob(blob)
    w = {k: v for k, v in _feeds(blob).items() if v[0] >= 6}
    want = {((s,), 2): (6, None) for s in pt.C_HP + pt.C_LP + pt.C_HPD}
    want[((pt.C_MIX,), 1)] = (7, ('curve', list(pt.LINE6_WET)))
    want[((pt.C_MIX,), 2)] = (7, ('curve', list(pt.LINE6_DRY)))
    want.update({((s,), 1): (8, ('select', [0, 4])) for s in pt.C_HPD})
    want[((pt.C_LPMIX,), 2)] = (8, ('select', [pt.f2u(pt.OFF_DB), pt.f2u(0.0)]))
    assert w == want
    dflt = {r[1]: blob.area[r[2] + 1] for r in blob.pins if blob.path(r[0]) == (0,)}
    assert (dflt[6], dflt[7], dflt[8]) == (pt.f2u(400.0), pt.f2u(mix), mode)


def test_c_new_members_are_registered(knobs):
    b0, blob = _blob0_and_teemah(knobs[1])
    blob = pt.Blob(blob)
    new = pt.C_HP + pt.C_LP + pt.C_HPD + (pt.C_MIX, pt.C_LPMIX)
    assert [c[0] for c in blob.root[2]] == [pt.C_SLOT_MAP[s] for s in (3, 6, 2, 4, 5)] + list(new)   # stock's, moved
    assert blob.sub_map() == blob.positions() and blob.pm[23] == 5 + len(new)
    _, ms = _members(b0, 0x600000 + b0[0xf] - 1)
    for s in new:
        assert {p: blob.area[v + 1] for path, p, v, _ in blob.pins if blob.path(path) == (s,)} == ms[s - 1][4]


def test_c_leaves_every_other_model_alone(knobs):
    stock, new, _, _ = knobs
    e3o, e3n = (kp.parse_entry3_full(kp.get_entry_swapped(x, 3)) for x in (stock, new))
    view = lambda info: [(m['name'], m['meta'], [(p['name'], p['type'], p['slot'], p['meta'], p['min'], p['max'])
                                                 for p in m['params']])
                         for m in info['models'] if m['name'] != pt.MODEL]
    assert repr(view(e3o)) == repr(view(e3n))
    wo, wn = (struct.unpack('<%dI' % (len(e) // 4), e) for e in
              (kp.get_entry_swapped(x, 4) for x in (stock, new)))
    teemah = kp.find_model(e3o, pt.MODEL)['blob_index']
    assert all(eb.blob(wo, i) == eb.blob(wn, i) for i in range(1, wo[2]) if i != teemah)


def test_line6_law_is_the_megaphones():
    """The Mix curves are the stock Megaphone's (and Chorus's, docs/research/sharc/mix-laws.md)."""
    stock = firmware.stock()
    e4 = kp.get_entry_swapped(stock, 4)
    w4 = struct.unpack('<%dI' % (len(e4) // 4), e4)
    m = kp.find_model(kp.parse_entry3_full(kp.get_entry_swapped(stock, 3)), 'HD2_DistMegaphoneMono')
    curves = {v[1][1] and tuple(round(x, 4) for x in v[1][1])
              for k, v in _feeds(pt.Blob(list(eb.blob(w4, m['blob_index'])))).items()
              if v[0] == 5 and v[1] and v[1][0] == 'curve' and k[0] == (12,)}
    assert curves == {pt.LINE6_WET, pt.LINE6_DRY}


def test_curve_at_interpolates_linearly():
    assert pt.curve_at(pt.LINE6_DRY, 0.0) == 0.0 and pt.curve_at(pt.LINE6_DRY, 1.0) == -120.0
    assert pt.curve_at(pt.LINE6_DRY, 0.5) == -3.0 and pt.curve_at(pt.LINE6_WET, 7 / 16) == pytest.approx(-4.5)


# Test build D (--levels): Freq, Dry Level, Wet Level, Mode on test build A's composite

@pytest.fixture(scope='module', params=[(20.0, -60.0, 0.0, 0), (400.0, 15.0, -20.0, 1)], ids=['full', 'crossover'])
def levels(request):
    stock = firmware.stock()
    freq, dry, wet, mode = request.param
    new = pt.patch_teemah_d(stock, freq=freq, dry_db=dry, wet_db=wet, mode=mode, log=lambda *_: None)
    return stock, new, freq, dry, wet, mode


def test_d_composite_is_a_with_the_defaults(levels):
    _, new, freq, dry, wet, mode = levels
    b0, _ = _blob0_and_teemah(new)
    h, ms = _members(b0, 0x600000 + b0[0xf] - 1)
    assert h[1] == 2 and h[2] == 13
    assert [t for t, *_ in ms] == [0, 0, 0x102, 0x102, 0x102, 0x6000c2, 0x102, 0x102, 0x102, 0x102, 0x84, 0x80, 0]
    lp = pt.FREQ_LP_SLOTS
    assert [ms[s - 1][4][1] for s in pt.FREQ_HP_SLOTS + lp] == [4, 4] + [3 if mode else 0] * 2
    assert all(ms[s - 1][4][2] == pt.f2u(freq) for s in pt.FREQ_HP_SLOTS + lp)
    assert ms[pt.D_MIX - 1][2:4] == ([0, 1], [0]) and ms[pt.D_MIX - 1][4] == {1: pt.f2u(wet), 2: pt.f2u(dry)}
    assert ms[pt.D_MIX][0] == 0x80                                                  # Level after the mixer
    assert kp.sharc_blob0_check(b0, kp.SHARC_BLOB0_MAX_WORDS) is None


def test_d_knobs_are_slots_6_to_9(levels):
    _, new, freq, dry, wet, mode = levels
    m = kp.find_model(kp.parse_entry3_full(kp.get_entry_swapped(new, 3)), pt.MODEL)
    assert [q['slot'] for q in m['params']] == list(range(1, 10)) and m['words'][6:8] == [9, 9]
    f, d, w, md = m['params'][5:]
    assert (f['name'], f['min'], f['max'], f['meta']['displayType']) == ('Frequency', 20.0, 1000.0, 'hz')
    for p, name, short, v, top in ((d, 'Dry Level', 'Dry Lvl', dry, 18.0), (w, 'Wet Level', 'Wet Lvl', wet, 9.0)):
        assert (p['name'], p['type'], p['min'], p['max'], p['meta']['displayType']) == (name, 3, -60.0, top, 'db')
        assert p['displayNameShort'] == short or p['meta'].get('displayNameShort') == short
        assert p['default'] == pytest.approx(v)
    assert (md['name'], md['type']) == ('Mode', 2)
    assert {k: v for k, v in md['meta'].items() if k.isdigit()} == {'0': 'Full', '1': 'Crossover'}
    assert struct.unpack_from('<8i', kp.get_entry_swapped(new, 3), m['words'][11] + 32 * 8)[3:] == (2, 9, 0, 1, mode)
    assert not kp.pmap_slots_without_param(kp.get_entry_swapped(new, 4), m['blob_index'], m['params'])


def test_d_entry_3_strings_are_stock(levels):
    import patch_comp
    assert patch_comp.e3_strings(kp.get_entry_swapped(levels[0], 3))[1:] == \
        patch_comp.e3_strings(kp.get_entry_swapped(levels[1], 3))[1:]


def test_d_knobs_write_filters_mixer_and_types(levels):
    _, new, freq, dry, wet, mode = levels
    _, blob = _blob0_and_teemah(new)
    blob = pt.Blob(blob)
    w = {k: v for k, v in _feeds(blob).items() if v[0] >= 6}
    want = {((s,), 2): (6, None) for s in pt.FREQ_HP_SLOTS + pt.FREQ_LP_SLOTS}
    want[((pt.D_MIX,), 2)] = (7, None)
    want[((pt.D_MIX,), 1)] = (8, None)
    want.update({((s,), 1): (9, ('select', [0, 3])) for s in pt.FREQ_LP_SLOTS})
    assert w == want
    dflt = {r[1]: blob.area[r[2] + 1] for r in blob.pins if blob.path(r[0]) == (0,)}
    assert (dflt[6], dflt[7], dflt[8], dflt[9]) == (pt.f2u(freq), pt.f2u(dry), pt.f2u(wet), mode)


def test_d_new_members_are_registered(levels):
    b0, blob = _blob0_and_teemah(levels[1])
    blob = pt.Blob(blob)
    new = pt.FREQ_HP_SLOTS + pt.FREQ_LP_SLOTS + (pt.D_MIX,)
    assert [c[0] for c in blob.root[2]] == [pt.SLOT_MAP[s] for s in (3, 6, 2, 4, 5)] + list(new)
    assert blob.sub_map() == blob.positions() and blob.pm[23] == 5 + len(new)
    _, ms = _members(b0, 0x600000 + b0[0xf] - 1)
    for s in new:
        assert {p: blob.area[v + 1] for path, p, v, _ in blob.pins if blob.path(path) == (s,)} == ms[s - 1][4]


def test_d_leaves_every_other_model_alone(levels):
    stock, new = levels[:2]
    e3o, e3n = (kp.parse_entry3_full(kp.get_entry_swapped(x, 3)) for x in (stock, new))
    view = lambda info: [(m['name'], m['meta'], [(p['name'], p['type'], p['slot'], p['meta'], p['min'], p['max'])
                                                 for p in m['params']])
                         for m in info['models'] if m['name'] != pt.MODEL]
    assert repr(view(e3o)) == repr(view(e3n))
    wo, wn = (struct.unpack('<%dI' % (len(e) // 4), e) for e in
              (kp.get_entry_swapped(x, 4) for x in (stock, new)))
    teemah = kp.find_model(e3o, pt.MODEL)['blob_index']
    assert all(eb.blob(wo, i) == eb.blob(wn, i) for i in range(1, wo[2]) if i != teemah)

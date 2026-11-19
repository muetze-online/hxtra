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

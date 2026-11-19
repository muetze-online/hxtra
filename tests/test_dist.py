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
    stock = firmware.stock()
    return (stock, pdist.patch_dist(stock, log=lambda *_: None),
            pt.patch_teemah_d(stock, log=lambda *_: None))       # test build D3, verified on the unit


@pytest.mark.firmware
def test_teemah_composite_is_test_build_d3s(built):
    _, new, d3 = built
    (bn, bd) = (eb.blob(_w4(x), 0) for x in (new, d3))
    hn, mn = _members(bn, eb.top_type(eb.blob(_w4(new), _model(new, TEEMAH)['blob_index'])))
    hd, md = _members(bd, 0x600000 + bd[0xf] - 1)
    assert hn[1:3] == hd[1:3] and mn == md


@pytest.mark.firmware
def test_teemah_blob_and_parameters_are_test_build_d3s(built):
    _, new, d3 = built
    i = _model(new, TEEMAH)['blob_index']
    assert eb.blob(_w4(new), i) == eb.blob(_w4(d3), i)
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
    assert {k for k, v in writes.items() if 'Frequency' in v} == {((s,), 2) for s in (3, 4, 9, 10)}
    assert {k for k, v in writes.items() if 'Dry Level' in v} == {((11,), 2)}
    assert {k for k, v in writes.items() if 'Wet Level' in v} == {((11,), 1)}
    assert {k for k, v in writes.items() if 'Mode' in v} == {((9,), 1), ((10,), 1)}
    assert {k for k, v in writes.items() if 'Level' in v} == {((12,), 1)}


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
        pdist.plan_model(fw, kp.find_model(fw.e3, 'HD2_DistPrizeDriveMono'))


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
    blobs = [[100 + k] * (3 + k) for k in range(6)]
    e4 = _e4(blobs, relocated)
    new0, new2, new5 = [7] * 9, [8] * 20, [9] * 8
    out, w = _blobs_of(pdist.e4_write(e4, new0, {2: new2, 5: new5}))
    assert out == [new0, blobs[1], new2, blobs[3], blobs[4], new5]
    wo = struct.unpack('<%dI' % (len(e4) // 4), e4)
    offs = lambda x, ks: [x[x[3] + 2 * k + 1] for k in ks]
    assert w[3] == wo[3]
    if relocated:                                        # Blob 0 grows where it was, nothing piles up behind
        assert offs(w, [0]) == offs(wo, [0])
        assert offs(w, [1, 3, 4]) == offs(wo, [1, 3, 4])
    else:                                                # stock: Blob 0 to the end, the model blobs stay put
        assert offs(w, [1, 3, 4, 5]) == offs(wo, [1, 3, 4, 5]) and offs(w, [0])[0] >= len(wo)


def test_e4_write_rewrites_a_blob_of_the_same_length_in_place():
    blobs = [[100 + k] * 4 for k in range(4)]
    e4 = _e4(blobs, False)
    wo = struct.unpack('<%dI' % (len(e4) // 4), e4)
    out, w = _blobs_of(pdist.e4_write(e4, blobs[0], {2: [5] * 4}))
    assert out[2] == [5] * 4 and w[w[3] + 5] == wo[wo[3] + 5]

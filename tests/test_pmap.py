"""tools/sharc/pmap.py: the map ops it evaluates, and what it reads from the stock file (docs/research/sharc/model-blobs.md)."""
import functools
import os

import pytest

import pmap


def knob(slot):
    return {'knob': 1, 'slot': slot}


def node(op, ins, data=None, table=None):
    e = {'op': hex(op), 'ins': {str(k): v for k, v in ins.items()}}
    if data is not None:
        e['data'] = data
    if table is not None:
        e['table'] = table
    return e


def test_curve_is_linear_between_its_nine_points():
    e = node(0x3, {0: knob(1)}, table=[0, 1, 2, 3, 4, 5, 6, 7, 16])
    assert pmap.evaluate(e, {1: 0.0}) == 0
    assert pmap.evaluate(e, {1: 0.5}) == 4
    assert pmap.evaluate(e, {1: 15 / 16}) == pytest.approx(11.5)
    assert pmap.evaluate(e, {1: 2.0}) == 16          # clamped to 0..1


def test_arithmetic_hot_variant_keeps_port_1_in_its_one_data_word():
    assert pmap.evaluate(node(0x14, {0: knob(1)}, data=[0, -1.0]), {1: 3.0}) == 2.0    # a + b
    assert pmap.evaluate(node(0x1d, {0: knob(1)}, data=[2.0]), {1: 3.0}) == 6.0        # hot a * b


def test_selects_and_conversions():
    sel = node(0x36, {0: knob(1)}, data=[10, 20, 30, 40])
    assert [pmap.evaluate(sel, {1: v}) for v in (0, 1, 3, 9)] == [10, 20, 40, 40]
    assert pmap.evaluate(node(0x27, {0: knob(1)}), {1: -20.0}) == pytest.approx(0.1)
    assert pmap.evaluate(node(0x27, {0: knob(1)}), {1: -120.0}) == 0.0
    assert pmap.evaluate(node(0x28, {0: knob(1)}), {1: 0.0}) == -120.0


def test_an_op_it_does_not_model_gives_none():
    assert pmap.evaluate(node(0x17, {0: knob(1)}), {1: 0.5}) is None


@functools.lru_cache(maxsize=None)
def firmware():
    path = os.environ.get('HX_STOMP_HXF')
    if not path:
        pytest.skip('HX_STOMP_HXF is not set')
    return pmap.Firmware(path)


def sweep_of(name):
    fw = firmware()
    r = pmap.model_report(fw, pmap.kp.find_model(fw.e3, name))
    return {(k['knob'], tuple(w['path']), w['pid']): w['values'] for k in pmap.sweep(r) for w in k['writes']}


@pytest.mark.firmware
def test_teemah_knobs_reach_their_members():
    # docs/research/sharc/cores/teemah.md, "Parameter map and blob init"
    s = sweep_of('Teemah')
    assert s[('Level', (6,), 1)] == [-60.0, -21.0, -6.0, 3.0, 9.0]
    assert s[('TrebleCut', (4,), 2)] == [10610.0, 4775.0, 1600.0, 671.0, 309.0]
    assert s[('Gain', (3, 6), 1)][0] == 0.0 and s[('Gain', (3, 6), 1)][-1] == 48.75   # through the drive's nested map
    assert s[('Clipping', (3, 7), 3)] == [-11.1, -11.1, -17.4]


@pytest.mark.firmware
def test_every_mono_distortion_and_eq_resolves():
    # model_report asserts one nested map (table F row) per composite member
    fw = firmware()
    ms = [m for m in fw.e3['models'] if m['meta'].get('category') in ('distortion', 'eq')
          and m['meta'].get('subcategory') == 'mono']
    assert len(ms) == 69
    for m in ms:
        pmap.model_report(fw, m)

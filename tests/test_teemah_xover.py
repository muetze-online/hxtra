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
    b0_old, blob_old = _blob0_and_teemah(built[0])
    b0, blob = _blob0_and_teemah(built[1])
    assert len(blob) == len(blob_old)
    assert blob[blob[2] + 1] == 0x600000 + b0[0xf] - 1
    changed = [k for k in range(len(blob)) if blob[k] != blob_old[k]]
    assert all(blob_old[k] in pt.SLOT_MAP and blob[k] == pt.SLOT_MAP[blob_old[k]] for k in changed
               if k != blob[2] + 1)


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

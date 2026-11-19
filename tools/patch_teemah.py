#!/usr/bin/env python3
"""patch_teemah.py IN OUT [--freq HZ] [--hp-type T] [--lp-type T] [--dry-db DB] [--wet-db DB] [--split] [--cost N]

Test build A for a Teemah! crossover (docs/research/sharc/cores/teemah.md), with stock primitives only and
fixed values (no new knobs):

  y = Level * (Teemah(HP(x)) + g_dry * LP(x))      (--split: g_dry * x, the LPs bypassed)

  Entry 4  Blob 0 gets a new composite (0x6001c6 on stock), a copy of Teemah!'s 0x6000cb with two buffers and
           six more members: a tap of the input into buffer 1, two 0x102 high-passes on buffer 0 before the
           drive, two 0x102 low-passes on buffer 1, and a 0x84 mixer [0, 1] -> 0 before Level. The new members'
           values are composite init records (Blob 0), so the model blob needs no new bindings: its path
           words and node tree are renumbered in place for the moved members (old slots 2..6 -> 5..8, 12) and
           its root points at the new composite. Blob 0 grows in place; the blobs after it move.
  Entry 2  the SHARC's Blob 0 buffer enlarged (kit_patch_amp.expand_sharc_blob0_buffer), as add-model does.
  Entry 0  Teemah!'s Static Model Table cost raised by --cost (default 100: a guess, not measured).

The 0x102 type numbers for second-order high/low-pass are not decoded yet: types 3 and 1 (a first-order pair,
which is which not known) are the defaults, so a wrong guess shows as the crossover mirrored on the unit.
With first-order sections, one a side (--sections 1, default) sums flat: 1/(1+s) + s/(1+s) = 1; two a side
(LR2) would cancel at f, (1+s^2)/(1+s)^2, so --sections 2 is only for second-order types.
"""
import argparse, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_patch_amp as kp
import entry4_blob
import l6ia
import patch_gate

MODEL = 'HD2_DistTeemahMono'
STOCK_COMP = 0x6000cb
SLOT_MAP = {2: 5, 3: 6, 4: 7, 5: 8, 6: 12}          # old top-level slot (path [1, k]) -> new
f2u = lambda x: struct.unpack('<I', struct.pack('<f', x))[0]


def build_members(b0, freq, hp_type, lp_type, dry_db, wet_db, split, sections=1):
    """[(rec template (20 words, offsets to be filled), ins, outs, init pairs)] of the new composite."""
    ct = b0[0x10]
    h = b0[ct + 7 * (STOCK_COMP - 0x600000): ct + 7 * (STOCK_COMP - 0x600000) + 7]
    assert h[:3] == [0, 1, 7], f'unexpected Teemah! composite header {h}'
    old = []
    for m in range(7):
        r = list(b0[h[3] + 20 * m: h[3] + 20 * m + 20])
        init = [tuple(b0[r[12] + 2 * k: r[12] + 2 * k + 2]) for k in range(r[11])]
        old.append((r, list(b0[r[8]:r[8] + r[7]]), list(b0[r[10]:r[10] + r[9]]), init))
    assert [o[0][6] for o in old] == [0, 0x102, 0x6000c2, 0x102, 0x102, 0x80, 0], 'unexpected Teemah! members'
    flt = old[1][0]                                          # a 0x102 record to copy (dm 9, pm 4)

    def prim(rec, ins, outs, init, typ=None, flags=0):
        r = list(rec)
        r[4], r[1], r[3] = flags, 0, 0
        if typ is not None:
            r[6] = typ
        return (r, ins, outs, init)

    def biquad(typ, buf, k):
        typ = typ if k < sections else 0                     # sections beyond --sections bypassed
        return prim(flt, [buf], [buf], [(1, typ), (2, f2u(freq)), (3, f2u(0.0)), (4, f2u(0.7071))])

    bypass = 0                                               # type 0: pass-through (teemah.md)
    mix = prim(old[5][0], [0, 1], [0], [(1, f2u(wet_db)), (2, f2u(dry_db))], typ=0x84)
    mix[0][13], mix[0][14] = 2, 2                            # 0x84: DM 2, PM 2 (registry row 0x200340)
    tap = prim(old[0][0], [0], [1], [], flags=1)
    tap[0][6] = 0
    members = [old[0],                                       # 0  in-port -> 0
               tap,                                          # 1  tap 0 -> 1 (dry)
               biquad(hp_type, 0, 0), biquad(hp_type, 0, 1), # 2, 3  high-passes on the wet path
               old[1], old[2], old[3], old[4],               # 4..7  Teemah!: input filter, drive, Treble, filter
               biquad(bypass if split else lp_type, 1, 0),   # 8, 9  low-passes on the dry path
               biquad(bypass if split else lp_type, 1, 1),
               mix,                                          # 10 [0, 1] -> 0
               old[5],                                       # 11 Level
               (old[6][0], [0], [], [])]                     # 12 out-port
    return members


def append_composite(b0, members, n_buf=2):
    """Blob 0 with a copy of the composite table one entry longer and the new composite after it."""
    b0 = list(b0)
    n_comp, ctab = b0[0xf], b0[0x10]
    new_ctab = len(b0)
    b0.extend(b0[ctab: ctab + 7 * n_comp])
    hdr_at = len(b0)
    b0.extend([0] * 7)
    mem_off = len(b0)
    b0.extend([0] * (20 * len(members)))
    for m, (rec, ins, outs, init) in enumerate(members):
        r = list(rec)
        r[0] = r[5] = m
        r[7], r[8] = len(ins), len(b0); b0.extend(ins)
        r[9], r[10] = len(outs), len(b0); b0.extend(outs)
        r[11], r[12] = len(init), len(b0); b0.extend(w for p in init for w in p)
        r[2] = len(b0)                                       # no connection records
        if not ins:
            r[8] = r[10]
        b0[mem_off + 20 * m: mem_off + 20 * m + 20] = r
    b0[hdr_at: hdr_at + 7] = [0, n_buf, len(members), mem_off, 0, len(b0), 0]
    b0[0xf], b0[0x10] = n_comp + 1, new_ctab
    err = kp.sharc_blob0_check(b0, kp.SHARC_BLOB0_MAX_WORDS)
    assert err is None, f'the SHARC would refuse this Blob 0: {err}'
    return b0, 0x600000 + n_comp


def renumber_blob(b, comp_id):
    """Model blob 92 with its root on comp_id and the moved members' slots renumbered (same length)."""
    b = list(b)
    tl, cnt = b[2], b[3]
    assert b[tl + 1] == STOCK_COMP
    b[tl + 1] = comp_id
    root_children = range(b[tl + 2], b[tl + 2] + 4 * b[tl + 3], 4)
    for o in root_children:
        b[o] = SLOT_MAP[b[o]]
    paths = set()
    pin, npin = b[10], b[11]
    paths.update(b[pin + 4 * k] for k in range(npin))
    po = b[6]
    pm = b[po: po + b[7]]
    a, nA = pm[0], pm[18]
    for k in range(nA):
        n = pm[a + 10 * k: a + 10 * k + 10]
        if n[3] == 1:                                        # a writer: data {path, pid, 1}
            paths.add(pm[n[0]])
    for p in sorted(paths):
        if b[p] >= 1 and b[p + 1] in SLOT_MAP:
            b[p + 1] = SLOT_MAP[b[p + 1]]
        else:
            assert b[p + 1] == 0, f'path at {p}: {b[p:p + 1 + b[p]]}'
    return b


def e4_with(e4, b0_new, blob_idx, blob_new):
    """Entry 4 with blob blob_idx rewritten in place (same length) and Blob 0 replaced: a new directory and
    Blob 0 at the tail, as kit_patch_amp.add_model_entry4 lays them out (stock's directory and Blob 0 stay
    behind unused; blobs added before, from index 864, move behind the new Blob 0)."""
    w = list(struct.unpack('<%dI' % (len(e4) // 4), e4))
    assert w[0] == 0x28267e5e and w[1] == 4 and w[5] == len(w)
    n, d = w[2], w[3]
    pairs = [(w[d + 2 * k], w[d + 2 * k + 1]) for k in range(n)]
    ln, off = pairs[blob_idx]
    assert len(blob_new) == ln and blob_idx < 864
    w[off: off + ln] = blob_new
    added = [w[o: o + l_] for (l_, o) in pairs[864:]]
    if d > 6:
        w = w[:d]
    new_d = len(w)
    end = new_d + 2 * n
    pairs[0] = (len(b0_new), end)
    w[4] = end
    end += len(b0_new)
    pairs = pairs[:864]
    for ab in added:
        pairs.append((len(ab), end))
        end += len(ab)
    for l_, o in pairs:
        w.extend((l_, o))
    w.extend(b0_new)
    for ab in added:
        w.extend(ab)
    w[3], w[5] = new_d, len(w)
    out = struct.pack('<%dI' % len(w), *w)
    assert len(out) <= kp.E4_MAX, f'Entry 4 {len(out):#x} bytes > {kp.E4_MAX:#x}'
    return out


def patch_teemah(l6ia_bytes, freq=400.0, hp_type=3, lp_type=1, dry_db=0.0, wet_db=0.0, split=False, cost=100,
                 sections=1, log=print):
    e0 = kp.get_entry_swapped(l6ia_bytes, 0)
    e3 = kp.get_entry_swapped(l6ia_bytes, 3)
    e4 = kp.get_entry_swapped(l6ia_bytes, 4)
    m = kp.find_model(kp.parse_entry3_full(e3), MODEL)
    w4 = struct.unpack('<%dI' % (len(e4) // 4), e4)
    b0 = entry4_blob.blob(w4, 0)
    members = build_members(b0, freq, hp_type, lp_type, dry_db, wet_db, split, sections)
    b0_new, comp = append_composite(b0, members)
    blob = renumber_blob(entry4_blob.blob(w4, m['blob_index']), comp)
    replaced = {4: e4_with(e4, b0_new, m['blob_index'], blob)}
    entries = l6ia.parse(l6ia_bytes)
    e2_raw = l6ia_bytes[entries[2]['payload_off']: entries[2]['payload_off'] + entries[2]['payload_len']]
    e2_exp, _ = kp.expand_sharc_blob0_buffer(e2_raw)
    replaced[2] = l6ia.swap32(e2_exp)
    replaced[0], old_cost = patch_gate.e0_add_cost(e0, m['words'][2], cost)
    log(f"[+] {MODEL}: composite {comp:#x} ({len(members)} members, 2 buffers), crossover {freq:g} Hz x{sections}, "
        f"HP type {hp_type}, {'split' if split else f'LP type {lp_type}'}, wet {wet_db:g} dB, dry {dry_db:g} dB; "
        f"Blob 0 {len(b0):#x} -> {len(b0_new):#x} words; DSP cost {old_cost} -> {old_cost + cost}")
    new, _, _ = l6ia.rebuild_archive(l6ia_bytes, replaced)
    return new


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input', help='stock .hxf or L6IA')
    ap.add_argument('output')
    ap.add_argument('--freq', type=float, default=400.0)
    ap.add_argument('--hp-type', type=int, default=3)
    ap.add_argument('--lp-type', type=int, default=1)
    ap.add_argument('--dry-db', type=float, default=0.0, help='gain of the dry path at the mixer (-120: off)')
    ap.add_argument('--wet-db', type=float, default=0.0)
    ap.add_argument('--split', action='store_true', help='dry path full band (low-passes bypassed)')
    ap.add_argument('--cost', type=int, default=100)
    ap.add_argument('--sections', type=int, choices=(1, 2), default=1,
                    help='filter sections a side (default 1: first-order types sum flat; 2 = LR2, notch at f)')
    ap.add_argument('--donor', help='stock .hxf, when input is a raw L6IA and output a .hxf')
    a = ap.parse_args()
    data, is_hxf = kp.load_l6ia_from_any(a.input)
    out = patch_teemah(data, a.freq, a.hp_type, a.lp_type, a.dry_db, a.wet_db, a.split, a.cost, a.sections)
    kp.save_l6ia_to_any(out, a.output, donor_hxf=a.input if is_hxf else a.donor)
    print(f'[+] Wrote {a.output}')


if __name__ == '__main__':
    main()

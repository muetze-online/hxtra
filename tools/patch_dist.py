#!/usr/bin/env python3
"""patch_dist.py IN OUT [--models NAME,...] [--cost N] [--donor STOCK.hxf]

Mono distortions with test build D's four knobs (docs/research/sharc/cores/teemah.md, Test build D), every model in
one pass:

  Full      (Mode 0): y = Level * (w * T(HP(x)) + d * x)        (the low-passes type 0)
  Crossover (Mode 1): y = Level * (w * T(HP(x)) + d * LP(x))

T is the stock block, HP and LP a 4th-order Linkwitz-Riley crossover at Freq (two 0x102 sections a side), w and d the
Wet and Dry Level. The knobs follow the stock ones: Freq (20..1000 Hz), Dry Level (-60..+18 dB), Wet Level (-60..+9 dB),
Mode (Full / Xover); the defaults (20 Hz, -60 dB, 0 dB, Full) sound like the stock block.

  Entry 4  Blob 0: one copy of the composite table for all new composites, one new composite per stock root (models
           that share a root share it): the stock members, a tap of the input into a new buffer, two high-passes after
           the in-port, two low-passes on the tap and a 0x84 mixer [wet, dry] -> wet right before the first member the
           Level knob writes (group G1: after it only gain members). Model blobs rebuilt with patch_teemah.Blob (root on
           the new composite, slots renumbered, four input nodes, new members registered). Written by e4_write: Blob 0
           grows in place when it is Entry 4's last region (an HXtra image) or moves to the end (stock); a grown model
           blob is laid out again in that region or appended; no other blob moves or is lost.
  Entry 3  per model the four parameters in one pass (e3_add_params): the parameter and state tables are copied once;
           Freq is the Auto Filter's record, Dry and Wet Double Double's, Mode a list of stock strings sharing one
           meta table.
  Entry 2  the SHARC's Blob 0 buffer enlarged (kit_patch_amp.expand_sharc_blob0_buffer, a no-op when done before).
  Entry 0  per model the UI row counts n + 4 and the Static Model Table cost + --cost (a guess until measured).

Run it after every step that adds models and before patch_comp.py (build.py, HX_DIST_XOVER)."""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [p for p in (HERE, os.path.join(HERE, 'sharc')) if p not in sys.path]
import kit_patch_amp as kp  # noqa: E402
import entry4_blob as eb  # noqa: E402
import l6ia  # noqa: E402
import patch_comp  # noqa: E402
import patch_gate  # noqa: E402
import patch_teemah as pt  # noqa: E402
import pmap  # noqa: E402

# D2: Teemah! and Scream 808 (one stock root, 0x6000cb); D3 widens this to every core mono distortion
MODELS = ('HD2_DistTeemahMono', 'HD2_DistScream808Mono')
LEVEL_KNOBS = ('Level',)
GAIN_TYPES = {0x80}                                    # members that may follow Level in group G1
FREQ, DRY, WET, MODE = 20.0, pt.LEVEL_MIN, 0.0, 0      # defaults: the stock sound
HP_TYPE, LP_TYPE, Q = 4, 3, 0.7071                     # LR4: two 2nd-order sections a side (patch_teemah)
N_KNOBS = 4
COST = 100
f2u = pt.f2u


class Fw:
    """pmap.Firmware over the entries of an image in memory."""
    def __init__(self, e3, w4):
        self.e3, self.w4, self.b0 = kp.parse_entry3_full(e3), w4, eb.blob(w4, 0)

    blob = pmap.Firmware.blob
    comp = pmap.Firmware.comp


def _knobs(e, acc):
    if isinstance(e, dict):
        if 'knob' in e:
            acc.add(e['knob'])
        for v in e.values():
            _knobs(v, acc)
    return acc


def plan_model(fw, m):
    """(stock root, slot of the first member Level writes) of a group G1 model, else a SystemExit."""
    r = pmap.model_report(fw, m)
    level = [p['disp'] for p in r['params'] if p['name'] in LEVEL_KNOBS]
    if len(level) != 1:
        raise SystemExit(f"{m['name']}: no single Level knob ({[p['name'] for p in r['params']]}): not group G1")
    slots = sorted({w['path'][0] for w in r['writes'] if w['path'] and level[0] in _knobs(w['expr'], set())})
    if not slots:
        raise SystemExit(f"{m['name']}: its Level knob writes no member")
    top = [c for c in r['chain'] if c['depth'] == 0]
    after = [c['type'] for c in top if c['path'][0] > slots[-1] and c['type'] != 'out-port']
    if any(int(t, 16) not in GAIN_TYPES for t in after if t.startswith('0x')) or 'tap' in after:
        raise SystemExit(f"{m['name']}: after Level come {after}: not group G1")
    return int(r['root'], 16), slots[0]


def _composite(b0, cid):
    ct = b0[0x10]
    h = b0[ct + 7 * (cid - 0x600000): ct + 7 * (cid - 0x600000) + 7]
    old = []
    for k in range(h[2]):
        r = list(b0[h[3] + 20 * k: h[3] + 20 * k + 20])
        assert r[2] == r[12] + 2 * r[11] or r[2] == max(r[8] + r[7], r[10] + r[9], r[12] + 2 * r[11]), \
            f'{cid:#x} member {k} has connection records'
        old.append((r, list(b0[r[8]: r[8] + r[7]]), list(b0[r[10]: r[10] + r[9]]),
                    [tuple(b0[r[12] + 2 * j: r[12] + 2 * j + 2]) for j in range(r[11])]))
    return h, old


def build_members(b0, root, level_slot):
    """(members, slot map old -> new, {'hp': slots, 'lp': slots, 'mix': slot, 'buffers': n}) of the new composite."""
    h, old = _composite(b0, root)
    _, tmpl = _composite(b0, pt.STOCK_COMP)                 # Teemah!'s: in-port, 0x102, 0x80 records to copy
    assert [t[0][6] for t in tmpl][:2] == [0, 0x102] and tmpl[5][0][6] == 0x80
    assert old[0][0][4] == 4 and old[-1][0][4] == 8, f'{root:#x}: no in-port first and out-port last'
    assert all(o[0][4] not in (4, 8) for o in old[1:-1]), f'{root:#x}: a port inside'
    assert len(old[0][2]) == 1, f'{root:#x}: in-port writes {old[0][2]}: not mono'
    L = level_slot
    assert 2 <= L < len(old), f'{root:#x}: Level slot {L}'
    wet_in, dry = old[0][2][0], h[1]
    lvl = old[L - 1]
    assert len(lvl[1]) == 1, f'{root:#x}: Level member reads {lvl[1]}'
    wet_at = lvl[1][0]

    def prim(rec, ins, outs, init, typ=None, flags=0):
        r = list(rec)
        r[4], r[1], r[3] = flags, 0, 0
        if typ is not None:
            r[6] = typ
        return (r, ins, outs, init)

    def biquad(typ, buf):
        return prim(tmpl[1][0], [buf], [buf], [(1, typ), (2, f2u(FREQ)), (3, f2u(0.0)), (4, f2u(Q))])

    lp_t = LP_TYPE if MODE else 0
    tap = prim(tmpl[0][0], [wet_in], [dry], [], flags=1)
    tap[0][6] = 0
    mix = prim(tmpl[5][0], [wet_at, dry], [wet_at], [(1, f2u(WET)), (2, f2u(DRY))], typ=0x84)
    mix[0][13], mix[0][14] = 2, 2                          # 0x84: DM 2, PM 2 (registry row 0x200340)
    members = ([old[0], tap, biquad(HP_TYPE, wet_in), biquad(HP_TYPE, wet_in)] + old[1:L - 1]
               + [biquad(lp_t, dry), biquad(lp_t, dry), mix] + old[L - 1:])
    smap = {s: s if s == 1 else s + 3 if s < L else s + 6 for s in range(1, len(old) + 1)}
    slots = {'hp': (3, 4), 'lp': (L + 3, L + 4), 'mix': L + 5, 'buffers': h[1] + 1}
    assert members[slots['mix'] - 1][0][6] == 0x84 and members[smap[L] - 1] is lvl and len(members) == len(old) + 6
    return members, smap, slots


def append_composites(b0, composites):
    """Blob 0 with one copy of the composite table, len(composites) entries longer, and the new composites after it;
    composites = [(members, n_buf)]. Returns (Blob 0, [new composite ids])."""
    b0 = list(b0)
    n_comp, ctab = b0[0xf], b0[0x10]
    new_ctab = len(b0)
    b0.extend(b0[ctab: ctab + 7 * n_comp])
    hdrs = len(b0)
    b0.extend([0] * (7 * len(composites)))
    ids = []
    for c, (members, n_buf) in enumerate(composites):
        mem_off = len(b0)
        b0.extend([0] * (20 * len(members)))
        for k, (rec, ins, outs, init) in enumerate(members):
            r = list(rec)
            r[0] = r[5] = k
            r[7], r[8] = len(ins), len(b0); b0.extend(ins)
            r[9], r[10] = len(outs), len(b0); b0.extend(outs)
            r[11], r[12] = len(init), len(b0); b0.extend(w for p in init for w in p)
            r[2] = len(b0)                                 # no connection records
            if not ins:
                r[8] = r[10]
            b0[mem_off + 20 * k: mem_off + 20 * k + 20] = r
        b0[hdrs + 7 * c: hdrs + 7 * c + 7] = [0, n_buf, len(members), mem_off, 0, len(b0), 0]
        ids.append(0x600000 + n_comp + c)
    b0[0xf], b0[0x10] = n_comp + len(composites), new_ctab
    err = kp.sharc_blob0_check(b0, kp.SHARC_BLOB0_MAX_WORDS)
    if err is not None:
        raise SystemExit(f'the SHARC would refuse this Blob 0 ({len(b0)} words): {err}')
    return b0, ids


def mode_meta(e3):
    """Entry 3 with the Mode list's meta table appended once, for every model's Mode record. (Entry 3, offset, S)"""
    e3 = bytearray(e3)
    _, _, _, S = patch_comp.e3_strings(e3)
    keys = ['displayName', 'displayNameShort', 'displayType', 'list', pt.MODE_NAME, pt.MODE_NAME + 'State']
    keys += [str(k) for k in range(len(pt.D_MODE_LABELS))] + list(pt.D_MODE_LABELS)
    missing = [s for s in keys if s not in S]
    assert not missing, f'Entry 3 lacks the strings {missing}'
    e3 += bytes((-len(e3)) % 4)
    meta = [('displayName', pt.MODE_NAME), ('displayNameShort', pt.MODE_NAME)]
    meta += [(str(k), v) for k, v in enumerate(pt.D_MODE_LABELS)] + [('displayType', 'list')]
    off = len(e3)
    e3 += b''.join(struct.pack('<3I', S[k], 7, S[v]) for k, v in meta)
    struct.pack_into('<I', e3, 8, len(e3))
    return bytes(e3), (off, len(meta)), S


def e3_add_params(e3, model_name, meta, S):
    """Entry 3 with Freq, Dry Level, Wet Level and Mode appended to model_name's parameters and states, its tables
    copied once (patch_comp.e3_add_knobs's layout); records as patch_teemah.e3_add_param / e3_add_list write them.
    Returns (Entry 3, first new slot)."""
    e3 = bytearray(e3)
    info = kp.parse_entry3_full(bytes(e3))
    m = kp.find_model(info, model_name)
    assert m['name'] == model_name
    d = list(m['words'])
    n_par, n_st = d[7], d[6]
    assert n_par == n_st and [p['slot'] for p in m['params']] == list(range(1, n_par + 1)), \
        f'{model_name}: parameters and states are not one per slot 1..{n_par}'
    new = [n for n in (pt.FREQ_SRC[1], pt.DRY_SRC[1], pt.WET_SRC[1], pt.MODE_NAME)]
    assert not {p['name'] for p in m['params']} & set(new), f'{model_name} has {new} already'
    params, states = [], []
    for k, (src_model, src_param, dflt, top) in enumerate((pt.FREQ_SRC + (FREQ, None), pt.DRY_SRC + (DRY, pt.DRY_MAX),
                                                           pt.WET_SRC + (WET, None))):
        src = kp.find_model(info, src_model)
        j = [p['name'] for p in src['params']].index(src_param)
        prec = list(struct.unpack_from('<8I', e3, src['words'][11] + 32 * j))
        srec = list(struct.unpack_from('<5I', e3, src['words'][10] + 20 * j))
        assert prec[3] == 3 and srec[3] == 3 and srec[4] == j, f'{src_model}.{src_param} is not a float of state {j}'
        prec[4], prec[7] = n_par + 1 + k, f2u(dflt)
        if top is not None:
            prec[6] = f2u(top)
        srec[4] = n_st + k
        params.append(struct.pack('<8I', *prec))
        states.append(struct.pack('<5I', *srec))
    meta_off, n_meta = meta
    params.append(struct.pack('<5I3i', n_meta, meta_off, S[pt.MODE_NAME], 2, n_par + 4, 0, len(pt.D_MODE_LABELS) - 1,
                              MODE))
    states.append(struct.pack('<5I', 0, 0x80b98, S[pt.MODE_NAME + 'State'], 2, n_st + 3))
    e3 += bytes((-len(e3)) % 4)
    d[10] = len(e3)
    e3 += bytes(e3[m['words'][10]: m['words'][10] + 20 * n_st]) + b''.join(states)
    d[11] = len(e3)
    e3 += bytes(e3[m['words'][11]: m['words'][11] + 32 * n_par]) + b''.join(params)
    d[6], d[7] = n_st + N_KNOBS, n_par + N_KNOBS
    struct.pack_into('<14I', e3, m['off'], *d)
    struct.pack_into('<I', e3, 8, len(e3))
    return bytes(e3), n_par + 1


def e4_write(e4, b0_new, blobs):
    """Entry 4 with Blob 0 replaced by b0_new and each {index: words} of blobs replaced. Blob 0 and the blobs laid out
    after it (an HXtra image: the relocated directory's tail) are laid out again from Blob 0's offset; on stock, where
    Blob 0 sits before the model blobs, Blob 0 goes to the end. A replaced blob before Blob 0 that keeps its length is
    rewritten in place, else appended. The directory stays where it is (its blob count does not change)."""
    w = list(struct.unpack('<%dI' % (len(e4) // 4), e4))
    assert w[0] == 0x28267e5e and w[1] == 4 and w[5] == len(w)
    n, d = w[2], w[3]
    pairs = [[w[d + 2 * k], w[d + 2 * k + 1]] for k in range(n)]
    content = {k: list(blobs.get(k, w[o: o + ln])) for k, (ln, o) in enumerate(pairs)}
    content[0] = list(b0_new)
    b0_off = pairs[0][1]
    tail = sorted((k for k, (_, o) in enumerate(pairs) if o > b0_off), key=lambda k: pairs[k][1])
    if d > 6 and b0_off > d:
        region = [0] + tail                               # HXtra: Blob 0 and everything after it laid out again
        end = b0_off
        w = w[:b0_off]
    else:
        region = [0]                                      # stock: Blob 0 to the end
        end = len(w)
    for k, words in blobs.items():
        if k in region:
            continue
        ln, o = pairs[k]
        if len(words) == ln:
            w[o: o + ln] = words
        else:
            region.append(k)
    for k in region:
        pairs[k] = [len(content[k]), end]
        w.extend(content[k])
        end += len(content[k])
    for k, (ln, o) in enumerate(pairs):
        w[d + 2 * k], w[d + 2 * k + 1] = ln, o
    w[4], w[5] = pairs[0][1], len(w)
    out = struct.pack('<%dI' % len(w), *w)
    if len(out) > kp.E4_MAX:
        raise SystemExit(f'Entry 4 would be {len(out):#x} bytes; its partition holds {kp.E4_MAX:#x}')
    return out


def patch_dist(l6ia_bytes, models=MODELS, cost=COST, log=print):
    e0 = kp.get_entry_swapped(l6ia_bytes, 0)
    e3 = kp.get_entry_swapped(l6ia_bytes, 3)
    e4 = kp.get_entry_swapped(l6ia_bytes, 4)
    w4 = struct.unpack('<%dI' % (len(e4) // 4), e4)
    fw = Fw(e3, w4)
    b0 = fw.b0
    info = fw.e3
    ms = [kp.find_model(info, name) for name in models]
    assert [m['name'] for m in ms] == list(models)
    plans = {m['name']: plan_model(fw, m) for m in ms}
    roots = {}
    for name, (root, lvl) in plans.items():
        assert roots.setdefault(root, lvl) == lvl, f'{name}: Level at {lvl}, its root {root:#x} has it at {roots[root]}'
    built = {root: build_members(b0, root, lvl) for root, lvl in roots.items()}
    b0_new, ids = append_composites(b0, [(mem, sl['buffers']) for mem, _, sl in built.values()])
    new_comp = dict(zip(built, ids))
    e3, meta, S = mode_meta(e3)
    blobs = {}
    for m in ms:
        root = plans[m['name']][0]
        _, smap, sl = built[root]
        members = built[root][0]
        n = len(m['params'])
        e3, first = e3_add_params(e3, m['name'], meta, S)
        s_freq, s_dry, s_wet, s_mode = range(first, first + N_KNOBS)
        blob = pt.Blob(eb.blob(w4, m['blob_index']))
        blob.renumber(smap, new_comp[root], old_comp=root)
        blob.add_param(s_freq, f2u(FREQ), [(s, 2, None) for s in sl['hp'] + sl['lp']])
        blob.add_param(s_dry, f2u(DRY), [(sl['mix'], 2, None)])
        blob.add_param(s_wet, f2u(WET), [(sl['mix'], 1, None)])
        blob.add_param(s_mode, MODE, [(s, 1, ('select', 0, LP_TYPE)) for s in sl['lp']],
                       input_op=patch_comp.INT_INPUT_OP)
        blob.register({s: members[s - 1][3] for s in sl['hp'] + sl['lp'] + (sl['mix'],)})
        assert blob.sub_map() == blob.positions(), f"{m['name']}: sub-bindings do not match the node tree"
        blobs[m['blob_index']] = blob.assemble()
        e0 = patch_comp.e0_set_param_counts(e0, m['words'][2], n, n + N_KNOBS)
        e0, old_cost = patch_gate.e0_add_cost(e0, m['words'][2], cost)
        log(f"[+] {m['displayName']}: composite {new_comp[root]:#x} on {root:#x} ({len(members)} members), mixer before "
            f"slot {smap[plans[m['name']][1]]}; Freq, Dry Level, Wet Level, Mode on slots {first}..{s_mode}; "
            f"DSP cost {old_cost} -> {old_cost + cost}")
    e4 = e4_write(e4, b0_new, blobs)
    chk = kp.parse_entry3_full(e3)
    for m in ms:
        c = kp.find_model(chk, m['name'])
        assert [p['slot'] for p in c['params']] == list(range(1, len(m['params']) + N_KNOBS + 1))
        assert not kp.pmap_slots_without_param(e4, m['blob_index'], c['params']), f"{m['name']}: a slot without param"
    entries = l6ia.parse(l6ia_bytes)
    e2_raw = l6ia_bytes[entries[2]['payload_off']: entries[2]['payload_off'] + entries[2]['payload_len']]
    e2_exp, _ = kp.expand_sharc_blob0_buffer(e2_raw)
    log(f"[+] {len(ms)} models on {len(built)} new composites; Blob 0 {len(b0)} -> {len(b0_new)} words "
        f"(limit {kp.SHARC_BLOB0_MAX_WORDS}, {kp.SHARC_BLOB0_MAX_WORDS - len(b0_new)} free); Entry 3 "
        f"{len(kp.get_entry_swapped(l6ia_bytes, 3)):#x} -> {len(e3):#x} bytes; Entry 4 {len(e4)} bytes "
        f"(limit {kp.E4_MAX})")
    new, _, _ = l6ia.rebuild_archive(l6ia_bytes, {0: e0, 2: l6ia.swap32(e2_exp), 3: e3, 4: e4})
    return new


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input', help='.hxf or L6IA')
    ap.add_argument('output')
    ap.add_argument('--models', help='comma-separated Entry 3 names (default: MODELS)')
    ap.add_argument('--cost', type=int, default=COST, help=f'DSP cost added per model (default {COST}, a guess)')
    ap.add_argument('--donor', help='Stock .hxf of the unit, when input is a raw L6IA and output a .hxf')
    a = ap.parse_args()
    data, is_hxf = kp.load_l6ia_from_any(a.input)
    models = tuple(a.models.split(',')) if a.models else MODELS
    kp.save_l6ia_to_any(patch_dist(data, models, a.cost), a.output, donor_hxf=a.input if is_hxf else a.donor)
    print(f'[+] Wrote {a.output}')


if __name__ == '__main__':
    main()

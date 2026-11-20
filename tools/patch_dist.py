#!/usr/bin/env python3
"""patch_dist.py IN OUT [--models NAME,...] [--cost N] [--donor STOCK.hxf]

Mono distortions with test build D's four knobs (docs/research/sharc/cores/teemah.md, Test build D), every model in
one pass:

  Full      (Mode 0): y = Level * (w * T(HP(x)) + d * x)        (the low-passes type 0)
  Crossover (Mode 1): y = Level * (w * T(HP(x)) + d * LP(x))

T is the stock block, HP and LP a 4th-order Linkwitz-Riley crossover at Freq (two 0x102 sections a side), w and d the
Wet and Dry Level. The knobs follow the stock ones: Freq (20..1000 Hz), Dry Level (-60..+18 dB), Wet Level (-60..+9 dB),
Mode (Full / Xover); the defaults (20 Hz, -60 dB, 0 dB, Full) sound like the stock block.

Where the mixer goes (GROUPS; docs/research/sharc/cores/teemah.md, Rollout D3):
  g1     right before the first member Level writes; after it only gain members (checked)
  g2     the same, linear filters after Level allowed: they colour the dry signal too
  g3     right before the out-port (a clipper follows Level); Level reaches the dry signal through the map:
         dry dB = Dry Level + curve(Level) - curve(Level's default), the model's own Level curve shifted
  g4     right before the out-port, no single Level knob; Level does not reach the dry signal
  xover  Obsidian 7000, whose Blend mixes its own clean path: Freq and Mode only, HP on the drive path, LP on the
         clean path right after the member that splits them; no tap, no mixer

  Entry 4  Blob 0: one copy of the composite table for all new composites, one new composite per stock root (models
           that share a root share it): the stock members, two low-passes from the in-port's buffer into a new one
           (the dry path, no copy member), two high-passes after them and a 0x84 mixer [wet, dry] -> wet right before the first member the
           Level knob writes (group G1: after it only gain members). Model blobs rebuilt with patch_teemah.Blob (root on
           the new composite, slots renumbered, four input nodes, new members registered). Written by e4_write: Blob 0
           grows in place when it is Entry 4's last region (an HXtra image) or moves to the end (stock); a grown model
           blob is laid out again in that region or appended; no other blob moves or is lost.
  Entry 3  per model the four parameters in one pass (e3_add_params): the parameter and state tables are copied once;
           Freq is the Auto Filter's record, Dry and Wet Double Double's, Mode a list of stock strings sharing one
           meta table.
  Entry 2  the SHARC's Blob 0 buffer enlarged (kit_patch_amp.expand_sharc_blob0_buffer, a no-op when done before).
  Entry 0  per model the UI row counts n + 4 and the Static Model Table cost + --cost (100: fits 8 Pocket Fuzz or 6 Dark Dove on the unit).

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

MODELS = None                                          # every core mono distortion (core_mono) but SKIP
SKIP = {                                               # core mono distortions left as they are, and why
    'HD2_DistClawthornDriveMono': 'dry path carries the block\'s own output on the unit (root on/off flags)',
    'HD2_DistBitcrusherMono': 'dry path carries the block\'s own output on the unit (root on/off flags)',
    'HD2_DistPocketFuzzMono': 'dry path follows the Drive knob on the unit (a tap writes the in-port\'s buffer)',
}
LEVEL_KNOBS = ('Level', 'Volume')                      # Entry 3 names; Volume shows as "Level"
GAIN_TYPES = {0x80}                                    # members that may follow Level in group G1
GROUPS = {                                             # (group, Level knob) where not (g1, LEVEL_KNOBS)
    'HD2_DistObsidian7000Mono': ('xover', None),
    'HD2_DistZeroAmpBassDIMono': ('g2', 'Level'),
    'HD2_DistHorizonDriveMono': ('g2', 'Level'),
    'HD2_DistAlpacaRougeMono': ('g2', 'Volume'),
    'HD2_DistLegendaryDriveMono': ('g2', 'Volume'),
    'HD2_DistToneSovereignMono': ('g1', 'Level 2'),    # Level 2 is its last member
    'HD2_DistAmpegScramblerODMono': ('g3', 'Level'),
    'HD2_DistPrizeDriveMono': ('g3', 'Level'),
    'HD2_DistClawthornDriveMono': ('g4', None),
    'HD2_DistKinkyBoostMono': ('g4', None),
    'HD2_DistVitalBoostMono': ('g4', None),
}
ALL_KNOBS, XOVER_KNOBS = ('freq', 'dry', 'wet', 'mode'), ('freq', 'mode')
# (parameter, state) names of the Mode knob; the second (stock strings of the Split XOver) where the model has a Mode
# of its own (Pillars), shown as "Crossover" / "Xover" there
MODE_NAMES = ((pt.MODE_NAME, pt.MODE_NAME + 'State'), ('Xover', 'XoverState'))
MODE_DISP2 = ('Crossover', 'Xover')
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


def core_mono(info, rows=None):
    """Entry 3 names of the core mono distortions (the legacy ones are HD2_DM4* and two others). With rows (the name
    string indices that have a Static Model Table row, table1_names) only those: an HXtra build removes Xenomorph Fuzz
    Mono's row for NAM A2 Lite (preset_id 555), so the model is no longer reachable."""
    return tuple(m['name'] for m in info['models'] if m['name'].startswith('HD2_Dist') and m['name'].endswith('Mono')
                 and (rows is None or m['words'][2] in rows))


def table1_names(e0):
    """Set of the name string indices in Entry 0's Static Model Table (Table 1)."""
    segs = kp._parse_e0_segments(e0)
    A = kp.e0_device(segs)
    fo = lambda va, ln=4: kp._e0_vaddr_to_foff(segs, va, ln)
    t1 = struct.unpack_from('<I', e0, fo(A.TAB1_BASE_PTRS[0]))[0]
    _, cnt = kp._decode_thumb_movw(e0[fo(A.TAB1_CNT_INSN): fo(A.TAB1_CNT_INSN) + 4])
    return {struct.unpack_from('<I', e0, fo(t1 + 12 * i))[0] for i in range(cnt)}


def plan_model(fw, m, group=None):
    """{'group', 'root', 'at', 'knobs', 'level'} of a model: `at` the slot the mixer goes before (g1..g4) or the
    member that splits drive and clean path (xover); 'level' (g3) = (Level's parameter slot, its curve shifted to
    0 dB at its default). A model that does not fit its group (GROUPS, else g1): SystemExit."""
    group, lname = (group, None) if group else GROUPS.get(m['name'], ('g1', None))
    r = pmap.model_report(fw, m)
    top = [c for c in r['chain'] if c['depth'] == 0]
    plan = {'group': group, 'root': int(r['root'], 16), 'knobs': XOVER_KNOBS if group == 'xover' else ALL_KNOBS,
            'level': None}
    assert top[0]['type'] == 'in-port' and top[-1]['type'] == 'out-port', f"{m['name']}: ports"
    if group == 'xover':
        wet_in = top[0]['out']
        split = [c for c in top[1:-1] if c['in'] == wet_in and c['out'] != wet_in]
        if not split or len(split[0]['out']) != 1:
            raise SystemExit(f"{m['name']}: no member splits a drive path off the in-port's buffer")
        plan['at'] = split[0]['path'][0]
        return plan
    if group == 'g4':
        plan['at'] = top[-1]['path'][0]
        return plan
    level = [p for p in r['params'] if (p['name'] == lname if lname else p['name'] in LEVEL_KNOBS)]
    if len(level) != 1:
        raise SystemExit(f"{m['name']}: no single Level knob ({[p['name'] for p in r['params']]}): not group {group}")
    lw = [w for w in r['writes'] if w['path'] and level[0]['disp'] in _knobs(w['expr'], set())]
    slots = sorted({w['path'][0] for w in lw})
    if not slots:
        raise SystemExit(f"{m['name']}: its Level knob writes no member")
    after = [c['type'] for c in top if c['path'][0] > slots[-1] and c['type'] != 'out-port']
    if group == 'g1' and (any(int(t, 16) not in GAIN_TYPES for t in after if t.startswith('0x')) or 'tap' in after):
        raise SystemExit(f"{m['name']}: after Level come {after}: not group G1")
    if group == 'g3':
        e = lw[0]['expr']
        if len(lw) != 1 or e.get('op') != '0x3' or e['ins'].get('0', {}).get('knob') != level[0]['disp']:
            raise SystemExit(f"{m['name']}: Level does not write one member through one curve: not group G3")
        assert (level[0]['min'], level[0]['max']) == (0.0, 1.0), f"{m['name']}: Level is not a 0..1 knob"
        ref = pt.curve_at(e['table'], level[0]['default'])
        plan['level'] = (level[0]['slot'], [v - ref for v in e['table']])
        plan['at'] = top[-1]['path'][0]
    else:
        plan['at'] = slots[0]
    return plan


def _composite(b0, cid):
    """(header, [(record, inputs, outputs, init pairs, connection)]) of composite cid. A member's connection (record
    word 1 its length, word 2 its offset) is either none or, in every core mono distortion that has one (Alpaca Rouge,
    Bitcrusher, Clawthorn, KWB, Obsidian 7000, Pocket Fuzz, Swedish Chainsaw), the 3 words [2, offset of the next two,
    0] and those two words [id, 0]; it is returned as the two words. The ids are the composite's own (header word 0
    counts them), not member slots, so they are copied as they are."""
    ct = b0[0x10]
    h = b0[ct + 7 * (cid - 0x600000): ct + 7 * (cid - 0x600000) + 7]
    old = []
    for k in range(h[2]):
        r = list(b0[h[3] + 20 * k: h[3] + 20 * k + 20])
        assert r[2] == r[12] + 2 * r[11] or r[2] == max(r[8] + r[7], r[10] + r[9], r[12] + 2 * r[11]), \
            f'{cid:#x} member {k}: connection list not after its data'
        conn = None
        if r[1]:
            w = list(b0[r[2]: r[2] + 5])
            assert r[1] == 3 and w[0] == 2 and w[1] == r[2] + 3 and w[2] == 0, \
                f'{cid:#x} member {k}: connection {r[1]} words {w}, not the one form patch_dist copies'
            conn = w[3:5]
        old.append((r, list(b0[r[8]: r[8] + r[7]]), list(b0[r[10]: r[10] + r[9]]),
                    [tuple(b0[r[12] + 2 * j: r[12] + 2 * j + 2]) for j in range(r[11])], conn))
    return h, old


def build_members(b0, root, plan):
    """(members, slot map old -> new, {'hp': slots, 'lp': slots, 'mix': slot or None, 'buffers': n}) of the new
    composite for plan_model's plan."""
    h, old = _composite(b0, root)
    _, tmpl = _composite(b0, pt.STOCK_COMP)                 # Teemah!'s: in-port, 0x102, 0x80 records to copy
    assert [t[0][6] for t in tmpl][:2] == [0, 0x102] and tmpl[5][0][6] == 0x80
    assert old[0][0][4] == 4 and old[-1][0][4] == 8, f'{root:#x}: no in-port first and out-port last'
    assert all(o[0][4] not in (4, 8) for o in old[1:-1]), f'{root:#x}: a port inside'
    assert len(old[0][2]) == 1, f'{root:#x}: in-port writes {old[0][2]}: not mono'
    L = plan['at']
    assert 2 <= L <= len(old), f'{root:#x}: mixer slot {L}'
    wet_in, dry = old[0][2][0], h[1]
    lvl = old[L - 1]
    assert len(lvl[1]) == 1, f'{root:#x}: the member at slot {L} reads {lvl[1]}'
    wet_at = lvl[1][0]

    def prim(rec, ins, outs, init, typ=None, flags=0):
        r = list(rec)
        r[4], r[1], r[3] = flags, 0, 0
        if typ is not None:
            r[6] = typ
        return (r, ins, outs, init, None)                  # no connection, as Teemah!'s members

    def biquad(typ, buf):
        return prim(tmpl[1][0], [buf], [buf], [(1, typ), (2, f2u(FREQ)), (3, f2u(0.0)), (4, f2u(Q))])

    lp_t = LP_TYPE if MODE else 0
    if plan['group'] == 'xover':                            # after the splitter: HP on its output, LP on its input
        drive, clean = lvl[2], lvl[1]
        assert len(drive) == 1 and clean == [wet_in] and drive != clean, f'{root:#x}: splitter {lvl[1]} -> {lvl[2]}'
        members = (old[:L] + [biquad(HP_TYPE, drive[0]), biquad(HP_TYPE, drive[0]),
                              biquad(lp_t, wet_in), biquad(lp_t, wet_in)] + old[L:])
        smap = {s: s if s <= L else s + 4 for s in range(1, len(old) + 1)}
        slots = {'hp': (L + 1, L + 2), 'lp': (L + 3, L + 4), 'mix': None, 'buffers': h[1], 'head': h[0]}
        assert len(members) == len(old) + 4 and all(members[s - 1][0][6] == 0x102 for s in slots['hp'] + slots['lp'])
        return members, smap, slots
    # The dry path starts with its first low-pass reading the in-port's buffer into the new one: no copy member. A
    # copy (tap) of the in-port's buffer carried that buffer's final content in Clawthorn Drive and Bitcrusher, the
    # two with root on/off flags (on the unit 2026-11-20, docs/research/sharc/cores/teemah.md, Rollout D3).
    lp1 = biquad(lp_t, dry)
    lp1 = (lp1[0], [wet_in], [dry], lp1[3], None)
    mix = prim(tmpl[5][0], [wet_at, dry], [wet_at], [(1, f2u(WET)), (2, f2u(DRY))], typ=0x84)
    mix[0][13], mix[0][14] = 2, 2                          # 0x84: DM 2, PM 2 (registry row 0x200340)
    members = ([old[0], lp1, biquad(lp_t, dry), biquad(HP_TYPE, wet_in), biquad(HP_TYPE, wet_in)] + old[1:L - 1]
               + [mix] + old[L - 1:])
    smap = {s: s if s == 1 else s + 4 if s < L else s + 5 for s in range(1, len(old) + 1)}
    slots = {'hp': (4, 5), 'lp': (2, 3), 'mix': L + 4, 'buffers': h[1] + 1, 'head': h[0]}
    assert members[slots['mix'] - 1][0][6] == 0x84 and members[smap[L] - 1] is lvl and len(members) == len(old) + 5
    assert all(m[0][4] != 1 for m in members[1:5]), 'a copy member among the new ones'
    return members, smap, slots


def append_composites(b0, composites):
    """Blob 0 with one copy of the composite table, len(composites) entries longer, and the new composites after it;
    composites = [(members, n_buf, header word 0)], members as _composite's (a connection is written after the
    member's init, its offset word pointing at its two words). Returns (Blob 0, [new composite ids])."""
    b0 = list(b0)
    n_comp, ctab = b0[0xf], b0[0x10]
    new_ctab = len(b0)
    b0.extend(b0[ctab: ctab + 7 * n_comp])
    hdrs = len(b0)
    b0.extend([0] * (7 * len(composites)))
    ids = []
    for c, (members, n_buf, head) in enumerate(composites):
        mem_off = len(b0)
        b0.extend([0] * (20 * len(members)))
        for k, (rec, ins, outs, init, conn) in enumerate(members):
            r = list(rec)
            r[0] = r[5] = k
            r[7], r[8] = len(ins), len(b0); b0.extend(ins)
            r[9], r[10] = len(outs), len(b0); b0.extend(outs)
            r[11], r[12] = len(init), len(b0); b0.extend(w for p in init for w in p)
            r[1], r[2] = (3 if conn else 0), len(b0)
            if conn:
                b0.extend([2, len(b0) + 3, 0] + list(conn))
            if not ins:
                r[8] = r[10]
            b0[mem_off + 20 * k: mem_off + 20 * k + 20] = r
        b0[hdrs + 7 * c: hdrs + 7 * c + 7] = [head, n_buf, len(members), mem_off, 0, len(b0), 0]
        ids.append(0x600000 + n_comp + c)
    b0[0xf], b0[0x10] = n_comp + len(composites), new_ctab
    err = kp.sharc_blob0_check(b0, kp.SHARC_BLOB0_MAX_WORDS)
    if err is not None:
        raise SystemExit(f'the SHARC would refuse this Blob 0 ({len(b0)} words): {err}')
    return b0, ids


def mode_meta(e3, disp=pt.MODE_NAME, short=pt.MODE_NAME):
    """Entry 3 with the Mode list's meta table appended once, for every model's Mode record. (Entry 3, offset, S)"""
    e3 = bytearray(e3)
    _, _, _, S = patch_comp.e3_strings(e3)
    keys = ['displayName', 'displayNameShort', 'displayType', 'list', disp, short]
    keys += [n for pair in MODE_NAMES for n in pair]
    keys += [str(k) for k in range(len(pt.D_MODE_LABELS))] + list(pt.D_MODE_LABELS)
    missing = [s for s in keys if s not in S]
    assert not missing, f'Entry 3 lacks the strings {missing}'
    e3 += bytes((-len(e3)) % 4)
    meta = [('displayName', disp), ('displayNameShort', short)]
    meta += [(str(k), v) for k, v in enumerate(pt.D_MODE_LABELS)] + [('displayType', 'list')]
    off = len(e3)
    e3 += b''.join(struct.pack('<3I', S[k], 7, S[v]) for k, v in meta)
    struct.pack_into('<I', e3, 8, len(e3))
    return bytes(e3), (off, len(meta)), S


def e3_add_params(e3, model_name, meta, S, knobs=ALL_KNOBS, mode_names=MODE_NAMES[0]):
    """Entry 3 with Freq, Dry Level, Wet Level and Mode (those of `knobs`, in this order, Mode last; Mode named
    mode_names) appended to model_name's parameters and states, its tables copied once (patch_comp.e3_add_knobs's
    layout); records as patch_teemah.e3_add_param / e3_add_list write them. Returns (Entry 3, first new slot)."""
    assert knobs in (ALL_KNOBS, XOVER_KNOBS)
    e3 = bytearray(e3)
    info = kp.parse_entry3_full(bytes(e3))
    m = kp.find_model(info, model_name)
    assert m['name'] == model_name
    d = list(m['words'])
    n_par, n_st = d[7], d[6]
    old = [p['slot'] for p in m['params']]
    assert n_par == n_st == len(old) and old == sorted(set(old)), f'{model_name}: parameters and states are not one per slot'
    first = old[-1] + 1                                     # slot ids may have gaps (252 stock models): after the last
    new = [n for n in (pt.FREQ_SRC[1], pt.DRY_SRC[1], pt.WET_SRC[1], mode_names[0])]
    assert not {p['name'] for p in m['params']} & set(new), f'{model_name} has {new} already'
    params, states = [], []
    floats = {'freq': pt.FREQ_SRC + (FREQ, None), 'dry': pt.DRY_SRC + (DRY, pt.DRY_MAX), 'wet': pt.WET_SRC + (WET, None)}
    n_new = len(knobs)
    for k, (src_model, src_param, dflt, top) in enumerate(floats[x] for x in knobs if x != 'mode'):
        src = kp.find_model(info, src_model)
        j = [p['name'] for p in src['params']].index(src_param)
        prec = list(struct.unpack_from('<8I', e3, src['words'][11] + 32 * j))
        srec = list(struct.unpack_from('<5I', e3, src['words'][10] + 20 * j))
        assert prec[3] == 3 and srec[3] == 3 and srec[4] == j, f'{src_model}.{src_param} is not a float of state {j}'
        prec[4], prec[7] = first + k, f2u(dflt)
        if top is not None:
            prec[6] = f2u(top)
        srec[4] = n_st + k
        params.append(struct.pack('<8I', *prec))
        states.append(struct.pack('<5I', *srec))
    meta_off, n_meta = meta
    params.append(struct.pack('<5I3i', n_meta, meta_off, S[mode_names[0]], 2, first + n_new - 1, 0,
                              len(pt.D_MODE_LABELS) - 1, MODE))
    states.append(struct.pack('<5I', 0, 0x80b98, S[mode_names[1]], 2, n_st + n_new - 1))
    e3 += bytes((-len(e3)) % 4)
    d[10] = len(e3)
    e3 += bytes(e3[m['words'][10]: m['words'][10] + 20 * n_st]) + b''.join(states)
    d[11] = len(e3)
    e3 += bytes(e3[m['words'][11]: m['words'][11] + 32 * n_par]) + b''.join(params)
    d[6], d[7] = n_st + n_new, n_par + n_new
    struct.pack_into('<14I', e3, m['off'], *d)
    struct.pack_into('<I', e3, 8, len(e3))
    return bytes(e3), first


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
    if models is None:
        reachable = core_mono(info, table1_names(e0))
        for name in sorted(set(core_mono(info)) - set(reachable)):
            log(f'[ ] {name}: no Static Model Table row (removed by this build), left as it is')
        models = tuple(n for n in reachable if n not in SKIP)
        for name in (n for n in reachable if n in SKIP):
            log(f'[ ] {name}: {SKIP[name]}, left as it is')
    ms = [kp.find_model(info, name) for name in models]
    assert [m['name'] for m in ms] == list(models)
    plans = {m['name']: plan_model(fw, m) for m in ms}
    roots = {}
    for name, p in plans.items():
        key = (p['group'] == 'xover', p['at'])
        assert roots.setdefault(p['root'], (key, p)) [0] == key, \
            f"{name}: {key} on root {p['root']:#x}, which another model has at {roots[p['root']][0]}"
    built = {root: build_members(b0, root, p) for root, (_, p) in roots.items()}
    b0_new, ids = append_composites(b0, [(mem, sl['buffers'], sl['head']) for mem, _, sl in built.values()])
    new_comp = dict(zip(built, ids))
    e3, meta, S = mode_meta(e3)
    meta2 = None
    blobs = {}
    for m in ms:
        p = plans[m['name']]
        root = p['root']
        members, smap, sl = built[root]
        n = len(m['params'])
        if MODE_NAMES[0][0] in {q['name'] for q in m['params']}:
            if meta2 is None:
                e3, meta2, _ = mode_meta(e3, *MODE_DISP2)
            e3, first = e3_add_params(e3, m['name'], meta2, S, p['knobs'], MODE_NAMES[1])
        else:
            e3, first = e3_add_params(e3, m['name'], meta, S, p['knobs'])
        s = dict(zip(p['knobs'], range(first, first + len(p['knobs']))))
        blob = pt.Blob(eb.blob(w4, m['blob_index']))
        blob.renumber(smap, new_comp[root], old_comp=root)
        blob.add_param(s['freq'], f2u(FREQ), [(k, 2, None) for k in sl['hp'] + sl['lp']])
        if 'dry' in s:
            how = None if p['level'] is None else ('plus_curve',) + tuple(p['level'])
            blob.add_param(s['dry'], f2u(DRY), [(sl['mix'], 2, how)])
            blob.add_param(s['wet'], f2u(WET), [(sl['mix'], 1, None)])
        blob.add_param(s['mode'], MODE, [(k, 1, ('select', 0, LP_TYPE)) for k in sl['lp']],
                       input_op=patch_comp.INT_INPUT_OP)
        new = sl['hp'] + sl['lp'] + ((sl['mix'],) if sl['mix'] else ())
        blob.register({k: members[k - 1][3] for k in new})
        assert blob.sub_map() == blob.positions(), f"{m['name']}: sub-bindings do not match the node tree"
        blobs[m['blob_index']] = blob.assemble()
        e0 = patch_comp.e0_set_param_counts(e0, m['words'][2], n, n + len(p['knobs']))
        e0, old_cost = patch_gate.e0_add_cost(e0, m['words'][2], cost)
        where = (f"crossover after slot {p['at']}" if sl['mix'] is None
                 else f"mixer before slot {smap[p['at']]}" + (' (dry follows Level)' if p['level'] else ''))
        log(f"[+] {m['displayName']} ({p['group']}): composite {new_comp[root]:#x} on {root:#x} ({len(members)} "
            f"members), {where}; {len(p['knobs'])} knobs on slots {first}..{first + len(p['knobs']) - 1}; "
            f"DSP cost {old_cost} -> {old_cost + cost}")
    e4 = e4_write(e4, b0_new, blobs)
    chk = kp.parse_entry3_full(e3)
    for m in ms:
        c = kp.find_model(chk, m['name'])
        old = [p['slot'] for p in m['params']]
        k = len(plans[m['name']]['knobs'])
        assert [p['slot'] for p in c['params']] == old + list(range(old[-1] + 1, old[-1] + 1 + k)), m['name']
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
    ap.add_argument('--cost', type=int, default=COST, help=f'DSP cost added per model (default {COST}, 8 Pocket Fuzz or 6 Dark Dove fit on the unit)')
    ap.add_argument('--donor', help='Stock .hxf of the unit, when input is a raw L6IA and output a .hxf')
    a = ap.parse_args()
    data, is_hxf = kp.load_l6ia_from_any(a.input)
    models = tuple(a.models.split(',')) if a.models else MODELS
    kp.save_l6ia_to_any(patch_dist(data, models, a.cost), a.output, donor_hxf=a.input if is_hxf else a.donor)
    print(f'[+] Wrote {a.output}')


if __name__ == '__main__':
    main()

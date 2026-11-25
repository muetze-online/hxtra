#!/usr/bin/env python3
"""patch_dist.py IN OUT [--models NAME,...] [--cost N] [--donor STOCK.hxf]

Mono distortions with Freq, Mix and Mode (test build D's knobs, docs/research/sharc/cores/teemah.md, Test build D, with
Mix in place of Dry and Wet Level: branch bass-mix), every model in one pass: the core mono distortions, the
legacy ones and Bronze Master (candidates), but SKIP:

  Full      (Mode 0): y = w * T(HP(x)) + d * L * x        (the low-passes type 0)
  Crossover (Mode 1): y = w * T(HP(x)) + d * L * LP(x)

T is the stock block with its own Level, HP and LP a 4th-order Linkwitz-Riley crossover at Freq (two 0x102 sections
a side), w and d the mixer's two gains from Mix (MIX_WET, MIX_DRY: 0 % the clean input at 0 dB, 100 % the stock
block, 50 % both equally loud), L the change of the block's Level from its default (the model's own Level curve, 0 dB
at the default), so Level sets the volume of both and Mix their ratio. The knobs follow the stock ones: Freq (Off,
20..1000 Hz), Mix (0..100 %, Megaphone's record; named Blend where the model has a Mix), Mode (Full / Xover); the
defaults (Off, 100 %, Full) sound like the stock block: at Freq's "Off" end (19.9 Hz, as stock Low Cut) the high-passes
take type 0 and pass the drive path through (an op 0x40 test of Freq into an op 0x2a select, Rollout D9).

Groups (docs/research/sharc/cores/teemah.md, Rollout D6):
  mix    the stock root nested whole in a new composite (outer_members): in-port, tap into the dry buffer, HP, the
         stock root, LP on the dry buffer, the 0x84 mixer, out-port. The stock members keep their buffers, taps and
         connections, out of the dry path's reach. Dry follows Level where Level writes one gain member through one
         curve or in dB (plan_model); LEVEL names other Level knobs, None where there is no single one (Dry alone).
  xover  Obsidian 7000, whose Blend mixes its own clean path: Freq and Mode only, HP on the drive path, LP on the
         clean path right after the member that splits them; no tap, no mixer (build_xover)

  Entry 4  Blob 0: one copy of the composite table for all new composites, one new composite per stock root (models
           that share a root share it); the record of the nested stock root sums its members (sub_record). Model
           blobs rebuilt with patch_teemah.Blob: group mix nested one level down in the stock form (nest_blob: a new
           root map passes each stock knob on to the nested stock map), group xover renumbered; three input nodes,
           new members registered. Written by e4_write: the whole Entry 4 laid out again without gaps (directory at
           word 6, blobs in index order), so no dead copy takes room in the partition; no blob is lost.
  Entry 3  per model the three parameters in one pass (e3_add_params): the parameter and state tables are copied once;
           Freq is the Auto Filter's record, Mix Megaphone's, Mode a list of stock strings sharing one meta table.
  Entry 2  the SHARC's Blob 0 buffer enlarged (kit_patch_amp.expand_sharc_blob0_buffer, a no-op when done before).
  Entry 0  per model the UI row counts n + 3 and the Static Model Table cost + --cost (100: fits 8 Pocket Fuzz or 6 Dark Dove on the unit).

Run it after every step that adds models and before patch_comp.py (build.py, HX_DIST_XOVER)."""
import argparse
import math
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

MODELS = None                                          # every candidate (candidates) but SKIP
# The legacy distortions, all mono, in the order of the unit's Distortion > Legacy browser list (Entry 0 list 10):
# fifteen HD2_DM4*, Bronze Master (its own Blend "mixture") and Killer Z, the last two on one root with a hidden
# Smart (slot 10000, no state, writes nothing); not Bass Octaver (pitch) nor Boost Comp and the other DM4 compressors
# (dynamics). Their volume knob is Output, Level (Bronze Master) or Gain (Killer Z, shown as "Level"), so LEVEL
# names it per model. Their Entry 3
# meta carries displayParamCount (Bronze Master, Killer Z also presetParamCount, without Smart); e3_show_all raises
# both by the new knobs, as Entry 0's row (rollout D5, docs/research/sharc/cores/teemah.md).
BRONZE, KILLER_Z = 'Line6BronzeMaster', 'KillerZ'
LEGACY = (
    'HD2_DM4TubeDrive', 'HD2_DM4Screamer', 'HD2_DM4Overdrive', 'HD2_DM4ClassicDistortion', 'HD2_DM4HeavyDistortion',
    'HD2_DM4ColorDrive', 'HD2_DM4BuzzSaw', 'HD2_DM4FacialFuzz', 'HD2_DM4JumboFuzz', 'HD2_DM4FuzzPi', 'HD2_DM4JetFuzz',
    'HD2_DM4Line6Drive', 'HD2_DM4Line6Distortion', 'HD2_DM4SubOctFuzz', 'HD2_DM4OctaveFuzz', BRONZE, KILLER_Z)
SKIP = {                                               # candidates left as they are, and why
    'HD2_DistKinkyBoostMono': 'a clean boost with no drive to split off, and no single Level (2026-11-22)',
    'HD2_DistVitalBoostMono': 'a clean boost with no drive to split off, and no single Level (2026-11-22)',
    'HD2_DistRegalBassDIMono': 'a clean bass DI with no drive to split off (2026-11-25)',
}
LEVEL_KNOBS = ('Level', 'Volume')                      # Entry 3 names; Volume shows as "Level"
LEVEL = {                                              # the Level knob where not LEVEL_KNOBS; None: no single one
    'HD2_DistAlpacaRougeMono': 'Volume',
    'HD2_DistLegendaryDriveMono': 'Volume',
    'HD2_DistToneSovereignMono': 'Level 2',            # Level 2 writes its last gain member
    'HD2_DistClawthornDriveMono': None,
    BRONZE: 'Level',
    KILLER_Z: 'Gain',                                  # Gain shows as "Level" and writes the last 0x80
}
LEVEL.update({n: 'Output' for n in LEGACY if n not in LEVEL})
GAIN = {'0x80': 'db', '0x225': 'lin'}                 # members whose pid 1 the Level knob sets: dB, linear
# Group xover, Freq and Mode only, and the form of its clean path (plan_model): 'split' (Obsidian 7000, whose Blend
# mixes its own clean path): a member splits the drive path off the in-port's buffer, which goes on clean;
# 'parallel' (Minotaur, Gain fading its clean paths): members read the in-port's buffer one after the other, the clean
# paths into buffers of their own, then the drive path (the one with the clipper 0x832) in place, nested composites
# all, so the mix group's nesting would put them three levels deep (silent on the unit, 2026-11-22)
XOVER = {'HD2_DistObsidian7000Mono': 'split', 'HD2_DistMinotaurMono': 'parallel'}
ALL_KNOBS, XOVER_KNOBS = ('freq', 'mix', 'mode'), ('freq', 'mode')
# (parameter, state) names of the Mode knob; the second (stock strings of the Split XOver) where the model has a Mode
# of its own (Pillars), shown as "Crossover" / "Xover" there
MODE_NAMES = ((pt.MODE_NAME, pt.MODE_NAME + 'State'), ('Xover', 'XoverState'))
MODE_DISP2 = ('Crossover', 'Xover')
FREQ, DRY, WET, MODE = 19.9, pt.LEVEL_MIN, 0.0, 0      # defaults: the stock sound (Freq Off)
MIX_DEFAULT = 1.0                                         # Mix's default: wet alone, the stock sound
# Mix m (0..1, shown in percent) writes both gains of the 0x84 mixer through 9-point curves (op 0x3, linear between
# m = 0, 1/8, .., 1): wet dB = C_w(m), dry dB = C_d(m) + w(m) * D(gain) + L(Level). C_w = dB(sin(pi m / 2) ^ 1.5) and
# C_d = dB(cos(pi m / 2) ^ 1.5), the curves in linear amplitude and op 0x28 to dB after them (curves in dB put the
# far end's -120 dB on a straight line to m = 1/8 and lose the first and last eighth: wet at m = 1/16 -71 dB instead
# of -30; in amplitude the error is 0.3 dB from m = 1/8 on and 3 dB at 1/16), a -4.5 dB law (each -4.5 dB at m = 0.5: between the -3 dB of uncorrelated and the
# -6 dB of correlated signals, as dry and driven share their fundamental), 0 (-120 dB) at the far end; w(m) = sin(pi m)
# up to m = 0.5, then 1, raises the dry signal by D (how much louder the drive is than the clean input, dB) on the
# way to the middle, so at 50 % both are equally loud and at 0 % the dry signal is the input at 0 dB. D is a curve
# on the model's gain knob (GAIN_KNOBS): MIX_DELTAS where measured, else constant MIX_DELTA.
MIX_FLOOR = -120.0                                     # op 0x28's dB for 0
MIX_DELTA = pt.DRY_MAX                                 # 18 dB, the old Dry Level's top
# D measured per model at the gain knob's 9 points 0, 1/8 .. 1 (0..1 knob): white noise at -24 dBFS RMS into USB 5/6
# (as loud below 1 kHz as the bass recording, peak -23.5 dBFS, -37.4 LUFS), Freq Off, Mode Full, level of Mix 100 % against
# the input over the third-octave bands 40..250 Hz, linear part plus distortion (tools/hx_xover_resp.py --sweep,
# 2026-11-25)
MIX_DELTAS = {'HD2_DistTeemahMono': [0.5, 1.6, 2.6, 3.1, 3.6, 4.2, 4.8, 5.8, 6.5]}
MIX_WET = [max(math.sin(math.pi * k / 16), 0.0) ** 1.5 for k in range(9)]           # linear amplitude
MIX_DRY = [max(math.cos(math.pi * k / 16), 0.0) ** 1.5 if k < 8 else 0.0 for k in range(9)]
MIX_RISE = [math.sin(math.pi * min(k / 8, 0.5)) for k in range(9)]
MIX_NAMES = ('Mix', 'Blend')                           # Entry 3 name: Blend where the model has a Mix of its own
GAIN_KNOBS = ('Gain', 'Drive', 'Fuzz', 'Sustain', 'Grit', 'Dist')   # D's knob: the first of these the model has
GAIN_KNOB = {'HD2_DistToneSovereignMono': 'Gain 2', 'HD2_DistClawthornDriveMono': None,   # else; None: D constant
             KILLER_Z: 'Dist'}                         # Killer Z's Gain is its Level
# Freq's "Off" end (as stock Low Cut, 19.9 Hz): the high-passes take type 0 (through) while Freq <= FREQ_ON, so the
# drive path is the stock block; above it they are the LR4 high-pass at Freq
FREQ_OFF, FREQ_ON = 19.9, 19.95
HP_TYPE, LP_TYPE, Q = 4, 3, 0.7071                     # LR4: two 2nd-order sections a side (patch_teemah)
N_KNOBS = 3
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
    """Entry 3 names of the core mono distortions (HD2_Dist*Mono; the legacy ones are LEGACY). With rows (the name
    string indices that have a Static Model Table row, table1_names) only those: an HXtra build removes Xenomorph Fuzz
    Mono's row for NAM A2 Lite (preset_id 555), so the model is no longer reachable."""
    return tuple(m['name'] for m in info['models'] if m['name'].startswith('HD2_Dist') and m['name'].endswith('Mono')
                 and (rows is None or m['words'][2] in rows))


def candidates(info, rows=None):
    """Entry 3 names of every model patch_dist may build (SKIP included): the core mono distortions, then LEGACY, each as Entry 3 has it and, with rows, a Static Model Table row (core_mono)."""
    have = {m['name']: m for m in info['models']}
    rest = tuple(n for n in LEGACY if n in have and (rows is None or have[n]['words'][2] in rows))
    return core_mono(info, rows) + rest


def table1_names(e0):
    """Set of the name string indices in Entry 0's Static Model Table (Table 1)."""
    segs = kp._parse_e0_segments(e0)
    A = kp.e0_device(segs)
    fo = lambda va, ln=4: kp._e0_vaddr_to_foff(segs, va, ln)
    t1 = struct.unpack_from('<I', e0, fo(A.TAB1_BASE_PTRS[0]))[0]
    _, cnt = kp._decode_thumb_movw(e0[fo(A.TAB1_CNT_INSN): fo(A.TAB1_CNT_INSN) + 4])
    return {struct.unpack_from('<I', e0, fo(t1 + 12 * i))[0] for i in range(cnt)}


def _level_terms(e, disp):
    """The Level knob's own terms in a gain writer's expression: ('curve', 9 values) for an op 0x3 curve on the
    knob, ('direct',) for the knob itself; through sums (0x14, 0x1b) and port 2 of the selects 0x2a, 0x2b (Wringer
    Fuzz, Ram's Head, Dark Dove: Level above 0.001 ? curve : -120 dB)."""
    if not isinstance(e, dict):
        return []
    if e.get('knob') == disp:
        return [('direct',)]
    op, ins = e.get('op'), e.get('ins', {})
    if op == '0x3' and ins.get('0', {}).get('knob') == disp:
        return [('curve', e['table'])]
    if op in ('0x14', '0x1b'):
        return _level_terms(ins.get('0'), disp) + _level_terms(ins.get('1'), disp)
    if op in ('0x2a', '0x2b'):
        return _level_terms(ins.get('2'), disp)
    return []


def plan_model(fw, m):
    """{'group', 'root', 'knobs', 'level', 'at'} of a model. Group 'mix': the stock root nested in a new composite
    with the crossover and the mixer around it (outer_members); 'level' = (Level's parameter slot, its curve shifted
    to 0 dB at its default, or None for a Level knob in dB with default 0), by which the dry signal follows Level, or
    None (LEVEL: no single Level knob). Group 'xover' (XOVER): its 'form', `at` the slot after which the filters go,
    'drive' the buffer the high-pass takes and 'clean' those the low-pass takes (build_xover). A model that does
    not fit: SystemExit."""
    r = pmap.model_report(fw, m)
    top = [c for c in r['chain'] if c['depth'] == 0]
    root = int(r['root'], 16)
    assert top[0]['type'] == 'in-port' and top[-1]['type'] == 'out-port', f"{m['name']}: ports"
    if m['name'] in XOVER:
        wet_in = top[0]['out']
        plan = {'group': 'xover', 'root': root, 'knobs': XOVER_KNOBS, 'level': None, 'form': XOVER[m['name']]}
        if XOVER[m['name']] == 'split':
            split = [c for c in top[1:-1] if c['in'] == wet_in and c['out'] != wet_in]
            if not split or len(split[0]['out']) != 1:
                raise SystemExit(f"{m['name']}: no member splits a drive path off the in-port's buffer")
            return dict(plan, at=split[0]['path'][0], drive=split[0]['out'], clean=wet_in)
        par = [c for c in top[1:-1] if c['in'] == wet_in]
        k = next((i for i, c in enumerate(par) if c['out'] == wet_in), None)
        clean = [b for c in par[:k] for b in c['out']] if k else []
        if (not k or par[k]['path'][0] != par[k - 1]['path'][0] + 1 or [c['path'][0] for c in par[:k]] !=
                list(range(2, 2 + k)) or len(clean) != k or len(set(clean) | {wet_in[0]}) != k + 1):
            raise SystemExit(f"{m['name']}: not clean paths off the in-port's buffer and then a drive path in place")
        return dict(plan, at=par[k]['path'][0] - 1, drive=wet_in, clean=clean)
    names = {p['name']: p['slot'] for p in m['params']}
    gname = GAIN_KNOB.get(m['name'], next((n for n in GAIN_KNOBS if n in names), None))
    plan = {'group': 'mix', 'root': root, 'knobs': ALL_KNOBS, 'level': None, 'at': None,
            'gain': None if gname is None else names[gname], 'delta': MIX_DELTAS.get(m['name'], [MIX_DELTA] * 9)}
    if m['name'] in MIX_DELTAS:
        g = next(p for p in r['params'] if p['name'] == gname)
        assert (g['min'], g['max']) == (0.0, 1.0), f"{m['name']}: measured D on {gname}, not a 0..1 knob"
    lname = LEVEL.get(m['name'], LEVEL_KNOBS)
    if lname is None:
        return plan
    level = [p for p in r['params'] if p['name'] in ((lname,) if isinstance(lname, str) else lname)]
    if len(level) != 1:
        raise SystemExit(f"{m['name']}: no single Level knob ({[p['name'] for p in r['params']]})")
    lv = level[0]
    gain = [w for w in r['writes'] if w['type'] in GAIN and w['pid'] == 1 and lv['disp'] in _knobs(w['expr'], set())]
    terms = [t for w in gain for t in _level_terms(w['expr'], lv['disp'])]
    if len(gain) != 1 or len(terms) != 1:
        raise SystemExit(f"{m['name']}: Level reaches {len(gain)} gain members through {len(terms)} terms, not one")
    if terms[0][0] == 'curve':
        assert (lv['min'], lv['max']) == (0.0, 1.0), f"{m['name']}: Level is not a 0..1 knob"
        table = terms[0][1]
        if GAIN[gain[0]['type']] == 'lin':                 # the legacy models' 0x225: linear gain, its 9 points in dB
            table = [20 * math.log10(v) if v > 1e-6 else -120.0 for v in table]
        ref = pt.curve_at(table, lv['default'])            # legacy: within 0.85 dB of the linear curve from 1/8 up
        plan['level'] = (lv['slot'], [v - ref for v in table])
    else:
        assert lv['min'] < 0.0 <= lv['max'] and lv['default'] == 0.0, f"{m['name']}: Level in dB, default not 0"
        plan['level'] = (lv['slot'], None)
    assert plan['gain'] != lv['slot'], f"{m['name']}: the gain knob is its Level"
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


def build_xover(b0, root, plan):
    """(members, slot map old -> new, {'hp': slots, 'lp': slots, 'mix': None, 'buffers': n, ..}) of group xover's
    new composite: the stock members and, right after slot plan['at'], HP on the drive buffer and LP on each clean
    one (XOVER: the splitter's drive output and the in-port's buffer, or the in-port's buffer that the drive path
    then takes in place and the buffers the clean paths wrote); no tap, no mixer."""
    h, old = _composite(b0, root)
    _, tmpl = _composite(b0, pt.STOCK_COMP)                 # Teemah!'s: in-port, 0x102, 0x80 records to copy
    assert [t[0][6] for t in tmpl][:2] == [0, 0x102] and tmpl[5][0][6] == 0x80
    assert old[0][0][4] == 4 and old[-1][0][4] == 8, f'{root:#x}: no in-port first and out-port last'
    assert all(o[0][4] not in (4, 8) for o in old[1:-1]), f'{root:#x}: a port inside'
    assert len(old[0][2]) == 1, f'{root:#x}: in-port writes {old[0][2]}: not mono'
    L = plan['at']
    assert 2 <= L <= len(old), f'{root:#x}: splitter slot {L}'
    wet_in = old[0][2][0]

    def prim(rec, ins, outs, init, typ=None, flags=0):
        r = list(rec)
        r[4], r[1], r[3] = flags, 0, 0
        if typ is not None:
            r[6] = typ
        return (r, ins, outs, init, None)                  # no connection, as Teemah!'s members

    def biquad(typ, buf):
        typ = 0 if typ == HP_TYPE and FREQ <= FREQ_ON else typ
        return prim(tmpl[1][0], [buf], [buf], [(1, typ), (2, f2u(FREQ)), (3, f2u(0.0)), (4, f2u(Q))])

    lp_t = LP_TYPE if MODE else 0
    drive, clean = plan['drive'], plan['clean']
    if plan['form'] == 'split':
        assert old[L - 1][2] == drive and old[L - 1][1] == clean == [wet_in], f'{root:#x}: splitter {old[L - 1][1:3]}'
    else:
        assert drive == [wet_in] and old[L][1] == old[L][2] == drive, f'{root:#x}: drive path {old[L][1:3]}'
    assert len(drive) == 1 and drive[0] not in clean and len(set(clean)) == len(clean), f'{root:#x}: {drive} {clean}'
    assert all(b < h[1] for b in drive + clean), f'{root:#x}: a buffer beyond its {h[1]}'
    new = [biquad(HP_TYPE, drive[0]) for _ in (0, 1)] + [biquad(lp_t, b) for b in clean for _ in (0, 1)]
    n = len(new)
    members = old[:L] + new + old[L:]
    smap = {s: s if s <= L else s + n for s in range(1, len(old) + 1)}
    slots = {'hp': (L + 1, L + 2), 'lp': tuple(range(L + 3, L + 1 + n)), 'mix': None, 'buffers': h[1],
             'head': h[0], 'list': (h[4], h[5])}
    assert len(members) == len(old) + n and all(members[s - 1][0][6] == 0x102 for s in slots['hp'] + slots['lp'])
    return members, smap, slots


S, HP, LP, MIX = 5, (3, 4), (6, 7), 8                  # group mix: slots of the outer composite (outer_members)
# A writer's data is {path, pid, kind}: kind 1 a parameter, 0 a switch of a composite (header word 0 counts them; a
# member's connection names one); an init record's word 3 is 1 for a parameter, 2 for a switch. Clawthorn Drive (Low
# Boost) and Bitcrusher (Low Cut, High Cut off) switch members of their stock root this way.
SWITCH, SWITCH_PIN = 0, 2


def sub_record(b0, cid):
    """Record words 13..18 of a member that is composite cid: DM (+1 per list entry), PM, word 15 (+ each list
    entry's word 3), 1 instance, its member count and list count, the counts of nested composites added (38 of the
    46 stock sub-composite members; the others are 2-member placeholders and two lists whose entries have word 1 = 16).
    A record that copied a 0x6000c2 member's words left the model silent on the unit (2026-11-22)."""
    ct = b0[0x10]
    h = b0[ct + 7 * (cid - 0x600000): ct + 7 * (cid - 0x600000) + 7]
    rs = [b0[h[3] + 20 * k: h[3] + 20 * k + 20] for k in range(h[2])]
    lst = [b0[h[5] + 4 * k: h[5] + 4 * k + 4] for k in range(h[4])]
    if any(e[1] != 1 for e in lst):
        raise SystemExit(f'{cid:#x}: list {lst}: record words unknown for that form')
    sub = [r for r in rs if r[4] == 0 and 0x600000 <= r[6] < 0x700000]
    return [sum(r[13] for r in rs) + h[4], sum(r[14] for r in rs), sum(r[15] for r in rs) + sum(e[3] for e in lst),
            1 + sum(r[16] for r in sub), len(rs) + sum(r[17] for r in sub), h[4] + sum(r[18] for r in sub)]


def outer_members(b0, root):
    """Members of group mix's new composite around stock root `root` (Teemah! test build D's layout, its stock
    members now the one composite member at slot S): 1 in-port -> [0], 2 tap [0] -> [1], 3, 4 HP on [0], 5 the
    stock root [0] -> [0], 6, 7 LP on [1], 8 0x84 [wet 0, dry 1] -> [0], 9 out-port [0]. The stock root stays as
    it is, with its own buffers, taps and connections, so nothing it does reaches the dry buffer: on the unit
    Thrifter Fuzz and Screamer lost the dry path and Pocket Fuzz's followed the high-pass while their members shared
    one composite with it (2026-11-22, measured: dry 0 dB flat, Xover an LR4 low-pass, off -58 dB)."""
    _, old = _composite(b0, root)
    _, tmpl = _composite(b0, pt.STOCK_COMP)          # Teemah!: in-port, 0x102, 0x6000c2, .., 0x80, out-port
    assert [t[0][6] for t in tmpl][:3] == [0, 0x102, 0x6000c2] and tmpl[5][0][6] == 0x80
    assert old[0][0][4] == 4 and old[-1][0][4] == 8, f'{root:#x}: no in-port first and out-port last'
    assert len(old[0][2]) == 1 and len(old[-1][1]) == 1, f'{root:#x}: not mono'

    def prim(rec, ins, outs, init, typ=None, flags=0):
        r = list(rec)
        r[4], r[1], r[3] = flags, 0, 0
        if typ is not None:
            r[6] = typ
        return (r, ins, outs, init, None)                  # no connection, as Teemah!'s members

    def biquad(typ, buf):
        typ = 0 if typ == HP_TYPE and FREQ <= FREQ_ON else typ
        return prim(tmpl[1][0], [buf], [buf], [(1, typ), (2, f2u(FREQ)), (3, f2u(0.0)), (4, f2u(Q))])

    lp_t = LP_TYPE if MODE else 0
    nest = prim(tmpl[2][0], [0], [0], [], typ=root)
    nest[0][13:19] = sub_record(b0, root)
    mix = prim(tmpl[5][0], [0, 1], [0], [(1, f2u(WET)), (2, f2u(DRY))], typ=0x84)
    mix[0][13], mix[0][14] = 2, 2                          # 0x84: DM 2, PM 2 (registry row 0x200340)
    members = [(old[0][0], [], [0], old[0][3], None), prim(tmpl[0][0], [0], [1], [], typ=0, flags=1),
               biquad(HP_TYPE, 0), biquad(HP_TYPE, 0), nest, biquad(lp_t, 1), biquad(lp_t, 1), mix,
               (old[-1][0], [0], [], old[-1][3], None)]
    assert members[S - 1][0][6] == root and members[MIX - 1][0][6] == 0x84
    assert all(members[k - 1][0][6] == 0x102 for k in HP + LP)
    return members


def f_row(pm, off):
    """Words 2..7 of the table F row for the map at pm offset `off`: its own s12, s13, nF, s14, s15, s16 + s17 plus
    the same words of each of its own filled F rows (148 rows in 74 stock model blobs; only the app harnesses, filled
    at run time, differ). A row of the map's own words alone left Minotaur, with three nested maps under the stock
    one, silent and hung the unit (2026-11-22)."""
    h = pm[off: off + 24]
    w = [h[12], h[13], h[23], h[14], h[15], h[16] + h[17]]
    for k in range(h[23]):
        r = pm[h[10] + 8 * k: h[10] + 8 * k + 8]
        if r[0]:
            w = [a + b for a, b in zip(w, r[2:8])]
    return w


def nest_blob(blob, outer_id):
    """Model blob `blob` (patch_teemah.Blob) one level down, in the stock form of a nested composite (Teemah!'s
    drive at slot 3): the root on outer_id with one child, slot S, holding the stock children; every stock member
    path (k, ..) -> (S, k, ..), init records and writers of all maps included; the stock map, shifted by the new
    root map's 24-word header, the nested map of table F's first row; a new root map with an input per stock knob
    (stock node order and Entry 3 indices kept) written on to (S,) pid slot, a hidden one (Bronze Master's Smart:
    no state, writes nothing) kept as its stock node without edges; init records (S,) slot with the knob defaults;
    sub-bindings (S, ..) -> (1, ..). Writers to the root's switches and their init records go to (S,), the stock
    root that holds the switched members (SWITCH; the stock App harness writes the switches of its child
    composites the same way)."""
    old = list(blob.pm)
    writers = list(blob.writer_data())
    member = {old[d] for d in writers} | {r[0] for r in blob.pins} | {p for p, _ in blob.sub}
    moved = {}
    for w in sorted(member):
        p = blob.path(w)
        if p != (0,):
            moved[w] = blob.add(len(p) + 1, S, *p)
    D = 24
    nested = list(old)

    def shift(base):                                       # pm-relative offsets of a map and its nested maps
        for k in (0, 2, 4, 6, 8, 10):
            nested[base + k] += D
        for k in range(old[base + 18]):
            nested[old[base] + 10 * k] += D
        for k in range(old[base + 19]):
            nested[old[base + 2] + 6 * k] += D
        f = old[base + 10]
        for k in range(old[base + 23]):
            if old[f + 8 * k]:
                nested[f + 8 * k] += D
                shift(old[f + 8 * k])
    shift(0)
    switches = []                                          # writers to the root's switches: on to the stock root
    for d in writers:
        if blob.path(old[d]) == (0,):
            assert old[d + 2] == SWITCH, f'a writer to the root with kind {old[d + 2]}, not a switch'
            switches.append(d)
        else:
            nested[d] = moved[old[d]]
    blob.reloc = [(t, r, o + D if r == 'pm' else o) for t, r, o in blob.reloc]
    frow = [D, 0] + f_row(old, 0)
    a, dn, ne = old[0], old[21], old[22]
    drows = [old[old[6] + 4 * k: old[6] + 4 * k + 4] for k in range(dn)]
    erows = [old[old[8] + 4 * k: old[8] + 4 * k + 4] for k in range(ne)]
    slot_of = {r[0]: r[2] for r in drows}
    pm = [0] * D + nested
    sp = blob.add(1, S)
    for d in switches:
        pm[D + d] = sp
    nodes, ct, new_id = [], [], {}
    for r in erows:                                        # an input and its writer per stock input, node order
        n = old[a + 10 * r[0]: a + 10 * r[0] + 10]
        assert n[8] == 1 and r[0] in slot_of, f'E row {r}: not an input with a slot'
        data = len(pm)
        pm += [sp, slot_of[r[0]], 1]
        i = new_id[r[0]] = len(nodes)
        nodes.append([data, 0, i, n[3], 1, 2, 0, 0, 1, 0])
        nodes.append([data, 0, i + 1, 1, 0, 3, 3, 3, 0, 0])
        ct.append([i, i + 1, 0, 0])
        blob.reloc.append((1, 'pm', data))
    for r in drows:                                        # a hidden knob: its stock node, no edges
        if r[0] not in new_id:
            assert r[2] >= pt.HIDDEN_SLOT, f'D row {r}: a shown knob without an input node'
            n = old[a + 10 * r[0]: a + 10 * r[0] + 10]
            new_id[r[0]] = len(nodes)
            nodes.append([n[0] + D, 0, len(nodes)] + n[3:])
    dt = [[new_id[r[0]], r[1], r[2], 0] for r in drows]
    et = [[new_id[r[0]], r[1], r[2], 0] for r in erows]
    pm[0] = len(pm); pm += [w for r in nodes for w in r]
    pm[2] = len(pm)
    pm[4] = len(pm); pm += [w for r in ct for w in r]
    pm[6] = len(pm); pm += [w for r in dt for w in r]
    pm[8] = len(pm); pm += [w for r in et for w in r]
    pm[10] = len(pm); pm += frow
    pm[18:24] = [len(nodes), 0, len(ct), len(dt), len(et), 1]
    pm[12:18] = patch_comp.pmap_sizes(pm)
    blob.pm = pm
    for r in blob.pins:
        if r[0] in moved:
            r[0] = moved[r[0]]
        elif blob.path(r[0]) == (0,) and r[3] == SWITCH_PIN:  # the root's switch inits go with its switches
            r[0] = sp
    shown = {slot_of[r[0]] for r in erows}
    for r in [r for r in blob.pins if blob.path(r[0]) == (0,) and r[1] in shown]:
        blob.pins.append([sp, r[1], r[2], 1])
        blob.reloc.append((1, 'pins', 4 * (len(blob.pins) - 1)))
    new_sub = [[sp, blob.add(1, 1)]]
    for p, q in blob.sub:
        pos = blob.path(q)
        new_sub.append([moved[p], blob.add(len(pos) + 1, 1, *pos)])
    blob.sub = new_sub
    blob.root = [blob.root[0], outer_id, [[S, 0xffffffff, blob.root[2]]]]


def append_composites(b0, composites):
    """Blob 0 with one copy of the composite table, len(composites) entries longer, and the new composites after it;
    composites = [(members, n_buf, header word 0, (header words 4, 5))], members as _composite's (a connection is
    written after the member's init, its offset word pointing at its two words). Header words 4/5 are the stock
    root's list (count, offset; 4 words an entry) that members name by record word 19 (1..count): the new header
    points at the stock list, which stays where it is. A build that wrote 0 there left Megaphone's member 0x2396
    naming entry 1 of an empty list ([1, 1, 0, 0xbc00] in stock) and hung the unit after a run of model changes
    (2026-11-21). Returns (Blob 0, [new composite ids])."""
    b0 = list(b0)
    n_comp, ctab = b0[0xf], b0[0x10]
    new_ctab = len(b0)
    b0.extend(b0[ctab: ctab + 7 * n_comp])
    hdrs = len(b0)
    b0.extend([0] * (7 * len(composites)))
    ids = []
    for c, (members, n_buf, head, (n_list, list_off)) in enumerate(composites):
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
        refs = max(rec[19] for rec, *_ in members)
        assert refs <= n_list, f'composite {c}: a member names list entry {refs} of {n_list}'
        b0[hdrs + 7 * c: hdrs + 7 * c + 7] = [head, n_buf, len(members), mem_off, n_list,
                                              list_off if n_list else len(b0), 0]
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


def freq_meta(e3):
    """Entry 3 with the Freq knob's meta table appended once: Auto Filter Frequency's (displayType hz, its short name)
    and valueMinString "Off", as stock Low Cut knobs show their 19.9 Hz end. (Entry 3, (offset, count))"""
    e3 = bytearray(e3)
    _, _, _, S = patch_comp.e3_strings(e3)
    assert {'valueMinString', 'Off'} <= set(S), 'Entry 3 lacks valueMinString or Off'
    src = kp.find_model(kp.parse_entry3_full(bytes(e3)), pt.FREQ_SRC[0])
    j = [p['name'] for p in src['params']].index(pt.FREQ_SRC[1])
    n, off = struct.unpack_from('<2I', e3, src['words'][11] + 32 * j)
    rows = bytes(e3[off: off + 12 * n])
    assert all(struct.unpack_from('<3I', rows, 12 * k)[0] != S['valueMinString'] for k in range(n))
    e3 += bytes((-len(e3)) % 4)
    at = len(e3)
    e3 += rows + struct.pack('<3I', S['valueMinString'], 7, S['Off'])
    struct.pack_into('<I', e3, 8, len(e3))
    return bytes(e3), (at, n + 1)


def e3_add_params(e3, model_name, meta, S, knobs=ALL_KNOBS, mode_names=MODE_NAMES[0], fmeta=None):
    """Entry 3 with Freq, Mix and Mode (those of `knobs`, in this order, Mode last; Mode named
    mode_names) added to model_name's parameters (at insert_at: before a hidden one on a slot from HIDDEN_SLOT, so
    the shown ones stay first) and appended to its states, its tables copied once (patch_comp.e3_add_knobs's layout);
    records as patch_teemah.e3_add_param / e3_add_list write them. Returns (Entry 3, first new slot)."""
    assert knobs in (ALL_KNOBS, XOVER_KNOBS)
    e3 = bytearray(e3)
    info = kp.parse_entry3_full(bytes(e3))
    m = kp.find_model(info, model_name)
    assert m['name'] == model_name
    d = list(m['words'])
    n_par, n_st = d[7], d[6]
    old = [p['slot'] for p in m['params']]
    at = insert_at(m['params'])
    assert n_par == len(old) and old == sorted(set(old)), f'{model_name}: parameters not one per slot'
    assert n_st in (n_par, at), f'{model_name}: {n_st} states for {n_par} parameters, not one each but hidden ones'
    first = old[at - 1] + 1                                 # slot ids may have gaps (252 stock models): after the last
    mix_name = mix_name_of(m)
    new = [n for n in (pt.FREQ_SRC[1], mix_name, mode_names[0])]
    assert not {p['name'] for p in m['params']} & set(new), f'{model_name} has {new} already'
    params, states = [], []
    floats = {'freq': pt.FREQ_SRC + (FREQ, None), 'mix': pt.MIX_SRC + (MIX_DEFAULT, None)}
    n_new = len(knobs)
    for k, (src_model, src_param, dflt, top) in enumerate(floats[x] for x in knobs if x != 'mode'):
        src = kp.find_model(info, src_model)
        j = [p['name'] for p in src['params']].index(src_param)
        prec = list(struct.unpack_from('<8I', e3, src['words'][11] + 32 * j))
        srec = list(struct.unpack_from('<5I', e3, src['words'][10] + 20 * j))
        assert prec[3] == 3 and srec[3] == 3 and srec[4] == j, f'{src_model}.{src_param} is not a float of state {j}'
        prec[4], prec[7] = first + k, f2u(dflt)
        if src_param == pt.FREQ_SRC[1] and fmeta is not None:     # the "Off" end below Auto Filter's 20 Hz
            prec[0], prec[1], prec[5] = fmeta[1], fmeta[0], f2u(FREQ_OFF)
        if top is not None:
            prec[6] = f2u(top)
        if src_param == pt.MIX_SRC[1] and mix_name != src_param:
            prec[2] = S[mix_name]
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
    po = m['words'][11]
    e3 += bytes(e3[po: po + 32 * at]) + b''.join(params) + bytes(e3[po + 32 * at: po + 32 * n_par])
    d[6], d[7] = n_st + n_new, n_par + n_new
    struct.pack_into('<14I', e3, m['off'], *d)
    struct.pack_into('<I', e3, 8, len(e3))
    return bytes(e3), first


def mix_name_of(m):
    """The Mix knob's Entry 3 name for model m: Mix, or Blend where m has a Mix of its own (Megaphone, Bitcrusher)."""
    have = {p['name'] for p in m['params']}
    return next(n for n in MIX_NAMES if n not in have)


def mix_exprs(plan):
    """('expr', tree) for the mixer's wet gain (pid 1) and dry gain (pid 2) from the Mix knob (MIX_WET, MIX_DRY,
    MIX_RISE, plan['delta'] on the gain knob); the dry signal follows Level as Dry Level did (plan['level'])."""
    m = ('in',)
    delta = (('curve', plan['delta'], ('knob', plan['gain'])) if plan.get('gain') is not None
             else ('curve', plan['delta'], m))
    dry = ('add', ('todb', ('curve', MIX_DRY, m)), ('mul', ('curve', MIX_RISE, m), delta))
    lv = plan['level']
    if lv is not None:
        dry = ('add', dry, ('knob', lv[0]) if lv[1] is None else ('curve', lv[1], ('knob', lv[0])))
    return ('expr', ('todb', ('curve', MIX_WET, m))), ('expr', dry)


HIDDEN_SLOT = pt.HIDDEN_SLOT                           # Bronze Master's Smart: hidden, no state, writes nothing
SHOWN_META = ('displayParamCount', 'presetParamCount')


def insert_at(params):
    """Index in a model's parameter list where patch_dist's knobs go: before the hidden ones on a slot from
    HIDDEN_SLOT (Entry 3 lists them last), else at the end. label_color colours the knobs from there."""
    at = len(params)
    while at and params[at - 1]['slot'] >= HIDDEN_SLOT:
        at -= 1
    return at


def e3_show_all(e3, model_name, k, S):
    """Entry 3 with model_name's meta displayParamCount and presetParamCount (where it has them) k higher, so the new
    knobs are shown and saved: its meta table copied once with those values replaced (strings of Entry 3). Core
    models have neither."""
    e3 = bytearray(e3)
    m = kp.find_model(kp.parse_entry3_full(bytes(e3)), model_name)
    if not any(key in SHOWN_META for _, key, *_ in m['meta_recs']):
        return bytes(e3)
    recs = []
    for off, key, kt, kv, val in m['meta_recs']:
        if key in SHOWN_META:
            assert kt == 7 and str(int(val) + k) in S, f'{model_name}: {key} {val!r} + {k} is no Entry 3 string'
            kv = S[str(int(val) + k)]
        recs.append(struct.pack('<3I', struct.unpack_from('<I', e3, off)[0], kt, kv))
    d = list(m['words'])
    e3 += bytes((-len(e3)) % 4)
    d[1] = len(e3)
    e3 += b''.join(recs)
    struct.pack_into('<14I', e3, m['off'], *d)
    struct.pack_into('<I', e3, 8, len(e3))
    return bytes(e3)


def e4_write(e4, b0_new, blobs):
    """Entry 4 with Blob 0 replaced by b0_new and each {index: words} of blobs replaced, laid out without gaps as
    e4_compact.compact does: the 6-word header, the directory at word 6, every blob in index order. Writing it compact
    keeps no dead copies (on a compact Entry 4 the old Blob 0 alone would leave ~2 MiB behind), so the partition check
    sees only live words; the build's last e4_compact changes nothing the blobs hold."""
    w = struct.unpack('<%dI' % (len(e4) // 4), e4)
    assert w[0] == 0x28267e5e and w[1] == 4 and w[5] == len(w)
    n, d = w[2], w[3]
    pairs = [(w[d + 2 * k], w[d + 2 * k + 1]) for k in range(n)]
    assert all(o + ln <= len(w) for ln, o in pairs), 'a blob reaches past the end of Entry 4'
    content = {k: blobs.get(k, w[o: o + ln]) for k, (ln, o) in enumerate(pairs)}
    content[0] = b0_new
    out = [0x28267e5e, 4, n, 6, 0, 0] + [0] * (2 * n)
    for k in range(n):
        out[6 + 2 * k: 8 + 2 * k] = [len(content[k]), len(out)]
        out.extend(content[k])
    out[4], out[5] = out[7], len(out)
    b = struct.pack('<%dI' % len(out), *out)
    if len(b) > kp.E4_MAX:
        raise SystemExit(f'Entry 4 would be {len(b):#x} bytes; its partition holds {kp.E4_MAX:#x}')
    return b


def patch_dist(l6ia_bytes, models=MODELS, cost=COST, log=print):
    e0 = kp.get_entry_swapped(l6ia_bytes, 0)
    e3 = patch_comp.e3_reclaim(kp.get_entry_swapped(l6ia_bytes, 3))   # NAM's dead stock tables, before appending
    e4 = kp.get_entry_swapped(l6ia_bytes, 4)
    w4 = struct.unpack('<%dI' % (len(e4) // 4), e4)
    fw = Fw(e3, w4)
    b0 = fw.b0
    info = fw.e3
    if models is None:
        reachable = candidates(info, table1_names(e0))
        for name in sorted(set(candidates(info)) - set(reachable)):
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
    mix_slots = {'hp': HP, 'lp': LP, 'mix': MIX, 'buffers': 2, 'head': 0, 'list': (0, 0)}
    built = {root: build_xover(b0, root, p) if p['group'] == 'xover' else (outer_members(b0, root), None, mix_slots)
             for root, (_, p) in roots.items()}
    b0_new, ids = append_composites(b0, [(mem, sl['buffers'], sl['head'], sl['list']) for mem, _, sl in built.values()])
    new_comp = dict(zip(built, ids))
    e3, meta, sm = mode_meta(e3)
    e3, fmeta = freq_meta(e3)
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
            e3, first = e3_add_params(e3, m['name'], meta2, sm, p['knobs'], MODE_NAMES[1], fmeta)
        else:
            e3, first = e3_add_params(e3, m['name'], meta, sm, p['knobs'], fmeta=fmeta)
        s = dict(zip(p['knobs'], range(first, first + len(p['knobs']))))
        blob = pt.Blob(eb.blob(w4, m['blob_index']))
        if p['group'] == 'xover':
            blob.renumber(smap, new_comp[root], old_comp=root)
        else:
            nest_blob(blob, new_comp[root])
        blob.add_param(s['freq'], f2u(FREQ), [(k, 2, None) for k in sl['hp'] + sl['lp']] +
                       [(k, 1, ('gt_select', FREQ_ON, 0, HP_TYPE)) for k in sl['hp']])
        if 'mix' in s:
            wet, dry = mix_exprs(p)
            blob.add_param(s['mix'], f2u(MIX_DEFAULT), [(sl['mix'], 1, wet), (sl['mix'], 2, dry)])
        blob.add_param(s['mode'], MODE, [(k, 1, ('select', 0, LP_TYPE)) for k in sl['lp']],
                       input_op=patch_comp.INT_INPUT_OP)
        new = sl['hp'] + sl['lp'] + ((sl['mix'],) if sl['mix'] else ())
        blob.register({k: members[k - 1][3] for k in new})
        assert blob.sub_map() == blob.positions(), f"{m['name']}: sub-bindings do not match the node tree"
        blobs[m['blob_index']] = blob.assemble()
        e3 = e3_show_all(e3, m['name'], len(p['knobs']), sm)
        e0 = patch_comp.e0_set_param_counts(e0, m['words'][2], n, n + len(p['knobs']), hidden_ok=True)
        e0, old_cost = patch_gate.e0_add_cost(e0, m['words'][2], cost)
        where = (f"crossover after slot {p['at']}" if sl['mix'] is None
                 else f"stock root nested at slot {S}" + (', dry follows Level' if p['level'] else ''))
        log(f"[+] {m['displayName']} ({p['group']}): composite {new_comp[root]:#x} on {root:#x} ({len(members)} "
            f"members), {where}; {len(p['knobs'])} knobs on slots {first}..{first + len(p['knobs']) - 1}; "
            f"DSP cost {old_cost} -> {old_cost + cost}")
    e4 = e4_write(e4, b0_new, blobs)
    chk = kp.parse_entry3_full(e3)
    for m in ms:
        c = kp.find_model(chk, m['name'])
        old = [p['slot'] for p in m['params']]
        k, at = len(plans[m['name']]['knobs']), insert_at(m['params'])
        assert [p['slot'] for p in c['params']] == old[:at] + list(range(old[at - 1] + 1, old[at - 1] + 1 + k)) + \
            old[at:], m['name']
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

#!/usr/bin/env python3
"""patch_teemah.py IN OUT [--levels | --knobs [--mix X]] [--mode Full|Crossover] [--freq HZ] [--hp-type T] [--lp-type T] [--dry-db DB] [--wet-db DB] [--split] [--cost N]
                         [--freq-knob [--unbound]]

Test builds for a Teemah! crossover (docs/research/sharc/cores/teemah.md), with stock primitives only.
A (default): fixed values, no new knobs. B (--freq-knob): a Freq knob on the four crossover filters.
C (--knobs): Freq, Mix and Mode knobs (patch_teemah_c, build_members_c).
D (--levels): Freq, Dry Level, Wet Level and Mode knobs on A's composite (patch_teemah_d).

  y = Level * (Teemah(HP(x)) + g_dry * LP(x))      (--split: g_dry * x, the LPs bypassed)

  Entry 4  Blob 0 gets a new composite (0x6001c6 on stock), a copy of Teemah!'s 0x6000cb with two buffers and
           six more members: a tap of the input into buffer 1, two 0x102 high-passes on buffer 0 before the
           drive, two 0x102 low-passes on buffer 1, and a 0x84 mixer [0, 1] -> 0 before Level. The new members'
           values are composite init records (Blob 0). The model blob is rebuilt (Blob): root on the new
           composite, member paths renumbered for the moved members (old slots 2..6 -> 5..8, 12), node-tree
           positions kept (docs/research/sharc/model-blobs.md, sub-bindings). It and a new Blob 0 go to Entry 4's
           tail (e4_with); the stock copies stay unused.
           B: a float input node for slot 6 feeding pid 2 of [1, 3], [1, 4], [1, 9], [1, 10]; by default those four
           are registered as stock bound members are (root children, sub-bindings, table F, init records),
           --unbound leaves them unregistered.
  Entry 3  B: slot 6 "Frequency", the Auto Filter's record (20..1000 Hz, Hz display), default --freq.
  Entry 2  the SHARC's Blob 0 buffer enlarged (kit_patch_amp.expand_sharc_blob0_buffer), as add-model does.
  Entry 0  Teemah!'s Static Model Table cost raised by --cost (default 100: a guess, not measured); B: its UI
           row counts 6 parameters.

0x102 types (docs/research/sharc/cores/filters.md): 1 / 2 = first-order low / high-pass, 3 / 4 = second-order
low / high-pass. Default: a 4th-order Linkwitz-Riley crossover as the stock Split crossover builds it, two
sections a side, HP type 4, LP type 3, Q 0.7071: -6.02 dB a band at f, 24 dB/oct, the bands sum flat (an
all-pass). First order: --sections 1 --hp-type 2 --lp-type 1 sums flat too, 1/(1+s) + s/(1+s) = 1; two
first-order sections a side (LR2) would cancel at f, (1+s^2)/(1+s)^2.
"""
import argparse, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_patch_amp as kp
import entry4_blob
import l6ia
import patch_comp
import patch_gate

MODEL = 'HD2_DistTeemahMono'
STOCK_COMP = 0x6000cb
SLOT_MAP = {2: 5, 3: 6, 4: 7, 5: 8, 6: 12}          # old top-level slot (path [1, k]) -> new
FREQ_SRC = ('HD2_FilterAutoFilterMono', 'Frequency')  # the Freq knob's record: float 20..1000 Hz, displayType hz
FREQ_MIN, FREQ_MAX = 20.0, 1000.0
FREQ_HP_SLOTS, FREQ_LP_SLOTS = (3, 4), (9, 10)        # members 2, 3 and 8, 9 of build_members
f2u = lambda x: struct.unpack('<I', struct.pack('<f', x))[0]

# Test build C (--knobs): Freq, Mix, Mode on a three-buffer composite (build_members_c)
C_SLOT_MAP = {2: 6, 3: 7, 4: 8, 5: 9, 6: 16}
C_HP, C_LP, C_HPD, C_MIX, C_LPMIX = (4, 5), (10, 11), (12, 13), 14, 15   # top-level slots (member index + 1)
MIX_SRC = ('HD2_DistMegaphoneMono', 'Mix')            # float 0..1, shown in percent (Chorus's has hidden sync slots)
MODE_NAME, MODE_LABELS = 'Mode', ('Full', 'Crossover')  # both strings exist in stock Entry 3; "Split" does not
LINE6_DRY = (0.0, 0.0, 0.0, 0.0, -3.0, -6.0, -12.0, -21.0, -120.0)   # the stock Mix law (docs/research/sharc/mix-laws.md)
LINE6_WET = LINE6_DRY[::-1]
OFF_DB = -120.0

# Test build D (--levels): Freq, Dry Level, Wet Level, Mode on test build A's composite (SLOT_MAP)
D_MIX = 11                                            # the 0x84 [0, 1] -> 0: pid 1 wet, pid 2 dry, in dB
DRY_SRC = ('HD2_DelayDoubleDoubleMono', 'Dry Level')  # float -60..+9 dB, displayType db
WET_SRC = ('HD2_DelayDoubleDoubleMono', 'Wet Level')
LEVEL_MIN, LEVEL_MAX = -60.0, 9.0                     # Double Double's range
D_MODE_LABELS = ('Full', 'Xover')                     # "Crossover" is cut short on the display; "Xover" is stock
DRY_MAX = 18.0                                        # Dry Level's top raised (bass): the drive is ~9..19 dB louder


def curve_at(table, x):
    """op 0x3: the 9-point table at x (0..1), linear between the points."""
    t = min(max(x, 0.0), 1.0) * 8
    i = min(int(t), 7)
    return table[i] + (table[i + 1] - table[i]) * (t - i)


def build_members(b0, freq, hp_type, lp_type, dry_db, wet_db, split, sections=2):
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


def build_members_c(b0, freq, hp_type, lp_type, mix, mode, sections=2):
    """Test build C's composite, three buffers: 0 the drive path, 1 the low band, 2 the dry path.

      Crossover (mode 1): y = Level * (g_w(mix) * T(HP(x)) + g_d(mix) * HPd(x) + LP(x))
      Full      (mode 0): y = Level * (g_w(mix) * T(HP(x)) + g_d(mix) * x)      (HPd type 0, the low band off)

    g_w, g_d: LINE6_WET, LINE6_DRY. HP, HPd, LP: the same crossover filters (sections each) at freq."""
    a = build_members(b0, freq, hp_type, lp_type, 0.0, 0.0, False, sections)
    tap, hp1, hp2, old1, old2, old3, old4, lp1, lp2, mix_rec, level, out = a[1:]
    tap2 = (list(tap[0]), [0], [2], [])
    hpd = [(list(r[0]), [2], [2], [(1, (hp_type if k < sections else 0) if mode else 0)] + r[3][1:])
           for k, r in enumerate((hp1, hp2))]
    g_w, g_d = curve_at(LINE6_WET, mix), curve_at(LINE6_DRY, mix)
    mixer = (list(mix_rec[0]), [0, 2], [0], [(1, f2u(g_w)), (2, f2u(g_d))])
    lpmix = (list(mix_rec[0]), [0, 1], [0], [(1, f2u(0.0)), (2, f2u(0.0 if mode else OFF_DB))])
    members = [a[0], tap, tap2, hp1, hp2, old1, old2, old3, old4, lp1, lp2, hpd[0], hpd[1], mixer, lpmix, level, out]
    assert [m[0][6] for m in members][C_MIX - 1] == 0x84 and len(members) == 17
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


HIDDEN_SLOT = 10000                     # slots from here: hidden parameters (Bronze Master's Smart)


class Blob:
    """A model blob taken apart so that its path area can grow, and laid out again by assemble() in stock order:
    header (12 words), path and value area, node tree (child blocks depth first), parameter map, init records,
    relocation list, sub-bindings. Offsets into the area stay put; the node tree's child offsets, the header and
    the relocation list follow the moved tables (relocations kept as (type, region, offset in region)).

    Sub-bindings (header words 8/9) are pairs {member path, node-tree position path}: in all 863 stock blobs, one per
    node-tree node under the root, the second path the node's position ([i] = the root's i-th child, [i, j] its j-th
    child) [verified offline]. Table F of the parameter map has one 8-word row per root child (863 of 863)."""

    def __init__(self, b):
        b = list(b)
        assert b[0] == 3, 'not a model blob'
        tl, nt, ini, nini, po, pl, sub, nsub, pin, npin = b[2:12]
        assert (tl + 4 * nt == po and po + pl == pin and pin + 4 * npin == ini and ini + 2 * nini == sub
                and sub + 2 * nsub == len(b)), 'unexpected blob layout'

        def node(o):
            s, t, co, cc = b[o: o + 4]
            return [s, t, [node(co + 4 * k) for k in range(cc)]]
        self.area = b[:tl]                                   # the header included, so area offsets are blob offsets
        self.root = node(tl)
        self.pm = b[po: po + pl]
        self.pins = [b[pin + 4 * k: pin + 4 * k + 4] for k in range(npin)]
        self.reloc = []
        for k in range(nini):
            t, off = b[ini + 2 * k: ini + 2 * k + 2]
            if po <= off < pin:
                self.reloc.append((t, 'pm', off - po))
            elif pin <= off < ini:
                self.reloc.append((t, 'pins', off - pin))
            else:
                assert off < tl, f'relocation at {off} is in no table'
                self.reloc.append((t, 'area', off))
        self.sub = [b[sub + 2 * k: sub + 2 * k + 2] for k in range(nsub)]
        assert all(w < tl for r in self.sub for w in r) and all(r[0] < tl and r[2] < tl for r in self.pins), \
            'a sub-binding or init record points past the area'

    def add(self, *words):
        """Append words to the area; their offset."""
        self.area += words
        return len(self.area) - len(words)

    def path(self, off):
        return tuple(self.area[off + 1: off + 1 + self.area[off]])

    def maps(self, base=0):
        """pm offsets of the parameter map's header and its nested maps' (table F rows), depth first."""
        yield base
        f, nf = self.pm[base + 10], self.pm[base + 23]
        for k in range(nf):
            if self.pm[f + 8 * k]:
                yield from self.maps(self.pm[f + 8 * k])

    def writer_data(self):
        """pm offsets of every writer's data {path, pid, 1}, nested maps' included."""
        for base in self.maps():
            a, na = self.pm[base], self.pm[base + 18]
            for k in range(na):
                n = self.pm[a + 10 * k: a + 10 * k + 10]
                if n[3] == 1:
                    yield n[0]

    def positions(self):
        """{member path: node-tree position path} as the node tree gives them."""
        out = {}

        def walk(n, mp, pp):
            for i, c in enumerate(n[2]):
                out[mp + (c[0],)] = pp + (i + 1,)
                walk(c, mp + (c[0],), pp + (i + 1,))
        walk(self.root, (), ())
        return out

    def sub_map(self):
        return {self.path(p): self.path(q) for p, q in self.sub}

    def assemble(self):
        out = list(self.area)
        tl = len(out)
        seq, child_off = [self.root], {}

        def visit(n):                                        # each node's children in one block, blocks depth first
            if n[2]:
                child_off[id(n)] = tl + 4 * len(seq)
                seq.extend(n[2])
                for c in n[2]:
                    visit(c)
        visit(self.root)
        for n in seq:
            out += [n[0], n[1], child_off.get(id(n), 0), len(n[2])]
        po = len(out)
        out += self.pm
        pin = len(out)
        for r in self.pins:
            out += r
        ini = len(out)
        base = {'pm': po, 'pins': pin, 'area': 0}
        for t, region, off in self.reloc:
            out += [t, base[region] + off]
        sub = len(out)
        for r in self.sub:
            out += r
        out[1:12] = [self.area[1], tl, len(seq), ini, len(self.reloc), po, len(self.pm), sub, len(self.sub), pin,
                     len(self.pins)]
        return out

    def renumber(self, slot_map, comp_id, old_comp=STOCK_COMP):
        """Root on comp_id, the root's children and every member path (init records, writers of all maps,
        sub-bindings' first paths) through slot_map. Position paths stay: a word that is both (in 836 of 863 stock
        blobs some are; Teemah!'s [2] ... [5]) gets its own copy for the position first."""
        assert self.root[1] == old_comp
        self.root[1] = comp_id
        for c in self.root[2]:
            c[0] = slot_map[c[0]]
        member = {self.pm[d] for d in self.writer_data()} | {r[0] for r in self.pins} | {p for p, _ in self.sub}
        for r in self.sub:
            if r[1] in member and self.path(r[1])[:1] and self.path(r[1])[0] in slot_map:
                r[1] = self.add(*self.area[r[1]: r[1] + 1 + self.area[r[1]]])
        for w in sorted(member):
            p = self.path(w)
            if p and p[0] in slot_map:
                self.area[w + 1] = slot_map[p[0]]
            else:
                assert p == (0,), f'path at {w}: {p}'

    def slot_path(self, s):
        """The area offset of path [s] for new writers and sub-bindings, one per slot."""
        paths = self.__dict__.setdefault('_paths', {})
        if s not in paths:
            paths[s] = self.add(1, s)
        return paths[s]

    def add_param(self, slot, default, outs, input_op=0xa):
        """An input node for parameter `slot` (op 0xa float, 0xb int; its state index the next) with a root default
        (a float word, or an int for 0xb), feeding a writer per (top-level slot, pid, how) of `outs`. how: None the
        value itself, ('curve', 9 values) through an op 0x3 node (a table B row), ('select', word if 0, word if not)
        through an op 0x2a node (port 0 ? port 2 : port 1, as Regal Bass DI switches its 0x102 types),
        ('plus_curve', other slot, 9 values) the value plus the other slot's knob through a new 0x3 node, added by an
        op 0x14 node (the dB sum by which patch_dist's Dry Level follows Level), ('plus', other slot) the same without
        the curve, ('gt_select', threshold, word if not, word if greater) the select fed by an op 0x40 test of the
        value against the threshold, ('expr', tree) a tree of curves, sums, products and dB conversions of this and other knobs
        (patch_dist's Mix). Tables A, C,
        D, E (and B when a curve is added) are rebuilt at the map's end, the old ones left unused."""
        pm = self.pm
        assert patch_comp.pmap_sizes(pm) == pm[12:18], 'the parameter map\'s sizes do not follow its tables'
        a, b, c, d, e = pm[0], pm[2], pm[4], pm[6], pm[8]
        na, nb, nc, nd, ne = pm[18], pm[19], pm[20], pm[21], pm[22]
        nodes = [pm[a + 10 * k: a + 10 * k + 10] for k in range(na)]
        bt = [pm[b + 6 * k: b + 6 * k + 6] for k in range(nb)]
        ct = [pm[c + 4 * k: c + 4 * k + 4] for k in range(nc)]
        dt = [pm[d + 4 * k: d + 4 * k + 4] for k in range(nd)]
        et = [pm[e + 4 * k: e + 4 * k + 4] for k in range(ne)]
        # ids may have gaps; a hidden parameter (slot from HIDDEN_SLOT: Bronze Master's Smart) stays last: the new one
        # goes before it in Entry 3 (patch_dist.e3_add_params), so its row (word 1 = its index there) moves up one
        shown = [r for r in dt if r[2] < HIDDEN_SLOT]
        assert slot < HIDDEN_SLOT and max(r[2] for r in shown) < slot, f'slot {slot} is not after the map\'s slots'
        hidden = [[r[0], r[1] + 1, r[2], r[3]] for r in dt if r[2] >= HIDDEN_SLOT]
        root = {r[0] for r in self.pins if r[3] == 1 and self.path(r[0]) == (0,)}
        assert len(root) == 1, 'no single root path among the init records'
        inp = len(nodes)
        nodes.append([nodes[0][0], 0, inp, input_op, 1, 2, 0, 0, 1, 0])
        dt = shown + [[inp, len(shown), slot, 0]] + hidden
        et.append([inp, ne, ne, 0])
        for s, _, _ in outs:
            self.slot_path(s)
        for s, pid, how in outs:
            src = inp
            if how is not None and how[0] == 'curve':
                assert len(how[1]) == 9
                data = len(pm)
                pm += [f2u(v) for v in how[1]]
                src = len(nodes)
                nodes.append([nodes[0][0], 0, src, 0x3, 1, 11, 0, 0, 0, 0])
                bt.append([data, 0, src, 0, 9, 0])
                ct.append([inp, src, 0, 0])
            elif how is not None and how[0] in ('plus_curve', 'plus'):
                # value + curve(parameter how[1]) through op 0x3 (how[2], 9 values) and op 0x14 (a + b); 'plus' the
                # parameter itself (a Level knob in dB: Deranged Master, Bronze Master)
                assert len(how) == (3 if how[0] == 'plus_curve' else 2)
                other = [r[0] for r in dt if r[2] == how[1]]
                assert len(other) == 1, f'no single input node for slot {how[1]}'
                cn = other[0]
                if how[0] == 'plus_curve':
                    assert len(how[2]) == 9
                    data = len(pm)
                    pm += [f2u(v) for v in how[2]]
                    cn = len(nodes)
                    nodes.append([nodes[0][0], 0, cn, 0x3, 1, 11, 0, 0, 0, 0])
                    bt.append([data, 0, cn, 0, 9, 0])
                    ct.append([other[0], cn, 0, 0])
                data = len(pm)
                pm += [0, 0]
                src = len(nodes)
                nodes.append([data, 0, src, 0x14, 1, 3, 2, 2, 0, 0])
                ct.append([inp, src, 0, 0])
                ct.append([cn, src, 0, 1])
            elif how is not None and how[0] == 'expr':
                # a tree of ('in',) this knob, ('knob', slot) another knob's input node, ('curve', 9 values, t)
                # op 0x3, ('add', a, b) op 0x14, ('mul', a, b) op 0x18 (stock's 676 nodes: 3 state words, data 0, 0),
                # ('todb', t) op 0x28, linear to dB, -120 for 0 (stock's 170 nodes: 2 state words, no data)
                def build(t):
                    if t[0] == 'in':
                        return inp
                    if t[0] == 'knob':
                        found = [r[0] for r in dt if r[2] == t[1]]
                        assert len(found) == 1, f'no single input node for slot {t[1]}'
                        return found[0]
                    if t[0] == 'curve':
                        assert len(t[1]) == 9
                        x = build(t[2])
                        data = len(pm)
                        pm.extend(f2u(v) for v in t[1])
                        n = len(nodes)
                        nodes.append([nodes[0][0], 0, n, 0x3, 1, 11, 0, 0, 0, 0])
                        bt.append([data, 0, n, 0, 9, 0])
                        ct.append([x, n, 0, 0])
                        return n
                    if t[0] == 'todb':
                        x = build(t[1])
                        n = len(nodes)
                        nodes.append([nodes[0][0], 0, n, 0x28, 1, 2, 0, 0, 0, 0])
                        ct.append([x, n, 0, 0])
                        return n
                    assert t[0] in ('add', 'mul') and len(t) == 3, t
                    a_, b_ = build(t[1]), build(t[2])
                    data = len(pm)
                    pm.extend([0, 0])
                    n = len(nodes)
                    nodes.append([data, 0, n, 0x14 if t[0] == 'add' else 0x18, 1, 3, 2, 2, 0, 0])
                    ct.append([a_, n, 0, 0])
                    ct.append([b_, n, 0, 1])
                    return n
                src = build(how[1])
            elif how is not None and how[0] == 'gt_select':
                # (value > how[1]) ? how[3] : how[2], through op 0x40 (a > b, b its data word, as Parametric EQ's Low
                # Cut tests 20 Hz) into an op 0x2a node: patch_dist's Freq "Off" end sets the high-passes' type 0
                assert len(how) == 4
                data = len(pm)
                pm += [f2u(how[1])]
                cmp = len(nodes)
                nodes.append([data, 0, cmp, 0x40, 1, 3, 1, 1, 0, 0])
                ct.append([inp, cmp, 0, 0])
                data = len(pm)
                pm += [how[2], how[3]]
                src = len(nodes)
                nodes.append([data, 0, src, 0x2a, 1, 4, 2, 2, 0, 0])
                ct.append([cmp, src, 0, 0])
            elif how is not None:
                assert how[0] == 'select' and len(how) == 3
                data = len(pm)
                pm += [how[1], how[2]]
                src = len(nodes)
                nodes.append([data, 0, src, 0x2a, 1, 4, 2, 2, 0, 0])
                ct.append([inp, src, 0, 0])
            data = len(pm)
            pm += [self.slot_path(s), pid, 1]
            nodes.append([data, 0, len(nodes), 1, 0, 3, 3, 3, 0, 0])
            ct.append([src, len(nodes) - 1, 0, 0])
            self.reloc.append((1, 'pm', data))
        tables = ((0, nodes),) + (((2, bt),) if len(bt) > nb else ()) + ((4, ct), (6, dt), (8, et))
        for k, rows in tables:                                       # D right before E: pmap_slots_without_param
            pm[k] = len(pm)
            pm += [w for r in rows for w in r]
        pm[18], pm[19], pm[20], pm[21], pm[22] = len(nodes), len(bt), len(ct), len(dt), len(et)
        self.pins.append([root.pop(), slot, self.add(1, default), 1])
        self.reloc.append((1, 'pins', 4 * (len(self.pins) - 1)))
        pm[12:18] = patch_comp.pmap_sizes(pm)

    def register(self, inits):
        """Register new top-level members as stock bound members are: per {slot: [(pid, value word), ..]} a root
        child, a sub-binding to its position, these init records, and a table F row (table F rebuilt at the end)."""
        pm = self.pm
        f, nf = pm[10], pm[23]
        assert nf == len(self.root[2]), 'table F does not have a row per root child'
        for s, vals in inits.items():
            self.root[2].append([s, 0xffffffff, []])
            self.sub.append([self.slot_path(s), self.add(1, len(self.root[2]))])
            for p, v in vals:
                self.pins.append([self.slot_path(s), p, self.add(1, v), 1])
                self.reloc.append((1, 'pins', 4 * (len(self.pins) - 1)))
        rows = pm[f: f + 8 * nf] + [0] * (8 * len(inits))
        pm[10] = len(pm)
        pm += rows
        pm[23] = nf + len(inits)
        pm[12:18] = patch_comp.pmap_sizes(pm)

    def add_knob(self, slot, default, targets, pid, input_op=0xa, bind=None):
        """Test build B: a float knob straight to pid of each top-level slot in `targets`; bind = {slot: inits}
        registers those members (register)."""
        self.add_param(slot, f2u(default), [(s, pid, None) for s in targets], input_op)
        if bind:
            self.register({s: bind[s] for s in targets})


def renumber_blob(b, comp_id):
    """Model blob 92 with its root on comp_id and the moved members' slots renumbered."""
    blob = Blob(b)
    blob.renumber(SLOT_MAP, comp_id)
    return blob


def e4_with(e4, b0_new, blob_idx, blob_new):
    """Entry 4 with blob blob_idx rewritten in place (same length; longer: at the tail) and Blob 0 replaced: a new
    directory and Blob 0 at the tail, as kit_patch_amp.add_model_entry4 lays them out (stock's directory and Blob 0 stay
    behind unused; blobs added before, from index 864, move behind the new Blob 0)."""
    w = list(struct.unpack('<%dI' % (len(e4) // 4), e4))
    assert w[0] == 0x28267e5e and w[1] == 4 and w[5] == len(w)
    n, d = w[2], w[3]
    pairs = [(w[d + 2 * k], w[d + 2 * k + 1]) for k in range(n)]
    ln, off = pairs[blob_idx]
    assert blob_idx < 864 and (d == 6 or off < d)
    moved = None
    if len(blob_new) == ln:
        w[off: off + ln] = blob_new
    else:
        moved = blob_new                                     # grown: to the tail, the old copy left unused
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
    if moved is not None:
        pairs[blob_idx] = (len(moved), end)
    for l_, o in pairs:
        w.extend((l_, o))
    w.extend(b0_new)
    for ab in added:
        w.extend(ab)
    if moved is not None:
        w.extend(moved)
    w[3], w[5] = new_d, len(w)
    out = struct.pack('<%dI' % len(w), *w)
    assert len(out) <= kp.E4_MAX, f'Entry 4 {len(out):#x} bytes > {kp.E4_MAX:#x}'
    return out


def e3_add_param(e3, model_name, src_model, src_param, default, top=None):
    """Entry 3 with a copy of src_model's parameter src_param (its record, meta and strings shared, so the string
    section stays as it is) appended to model_name's parameters and states, default (and top, the maximum) changed; the model's parameter
    and state tables are rewritten past the end (patch_comp.e3_add_knobs's layout). (Entry 3, new slot)."""
    e3 = bytearray(e3)
    info = kp.parse_entry3_full(bytes(e3))
    m, src = kp.find_model(info, model_name), kp.find_model(info, src_model)
    d = list(m['words'])
    n_par, n_st = d[7], d[6]
    assert n_par == n_st and [p['slot'] for p in m['params']] == list(range(1, n_par + 1)), \
        f'{model_name}: parameters and states are not one per slot 1..{n_par}'
    k = [p['name'] for p in src['params']].index(src_param)
    prec = list(struct.unpack_from('<8I', e3, src['words'][11] + 32 * k))
    srec = list(struct.unpack_from('<5I', e3, src['words'][10] + 20 * k))
    assert prec[3] == 3 and srec[3] == 3 and srec[4] == k, f'{src_model}.{src_param} is not a float of state {k}'
    assert all(p['name'] != src_param for p in m['params']), f'{model_name} has a {src_param} already'
    prec[4], prec[7] = n_par + 1, f2u(default)
    if top is not None:
        prec[6] = f2u(top)
    srec[4] = n_st
    base = len(e3) + (-len(e3)) % 4
    e3 += bytes(base - len(e3))
    d[10] = len(e3)
    e3 += bytes(e3[m['words'][10]: m['words'][10] + 20 * n_st]) + struct.pack('<5I', *srec)
    d[11] = len(e3)
    e3 += bytes(e3[m['words'][11]: m['words'][11] + 32 * n_par]) + struct.pack('<8I', *prec)
    d[6], d[7] = n_st + 1, n_par + 1
    struct.pack_into('<14I', e3, m['off'], *d)
    struct.pack_into('<I', e3, 8, len(e3))
    assert len(e3) <= kp.E3_MAX, f'Entry 3 {len(e3):#x} bytes > {kp.E3_MAX:#x}'
    return bytes(e3), n_par + 1


def e3_add_list(e3, model_name, name, labels, default):
    """Entry 3 with an integer list parameter appended to model_name (patch_comp.e3_add_knobs's records), built from
    strings Entry 3 already has, so its string section stays as it is: name (also its display names), name + 'State',
    the labels. (Entry 3, new slot)."""
    e3 = bytearray(e3)
    _, _, _, S = patch_comp.e3_strings(e3)
    keys = ['displayName', 'displayNameShort', 'displayType', 'list', name, name + 'State']
    keys += [str(k) for k in range(len(labels))] + list(labels)
    missing = [s for s in keys if s not in S]
    assert not missing, f'Entry 3 lacks the strings {missing}'
    info = kp.parse_entry3_full(bytes(e3))
    m = kp.find_model(info, model_name)
    d = list(m['words'])
    n_par, n_st = d[7], d[6]
    assert n_par == n_st and [p['slot'] for p in m['params']] == list(range(1, n_par + 1))
    assert all(p['name'] != name for p in m['params']), f'{model_name} has a {name} already'
    base = len(e3) + (-len(e3)) % 4
    e3 += bytes(base - len(e3))
    meta = [('displayName', name), ('displayNameShort', name)] + [(str(k), v) for k, v in enumerate(labels)]
    meta += [('displayType', 'list')]
    meta_off = len(e3)
    e3 += b''.join(struct.pack('<3I', S[k], 7, S[v]) for k, v in meta)
    d[10] = len(e3)
    e3 += bytes(e3[m['words'][10]: m['words'][10] + 20 * n_st]) + struct.pack('<5I', 0, 0x80b98, S[name + 'State'], 2,
                                                                            n_st)
    d[11] = len(e3)
    e3 += bytes(e3[m['words'][11]: m['words'][11] + 32 * n_par]) + struct.pack(
        '<5I3i', len(meta), meta_off, S[name], 2, n_par + 1, 0, len(labels) - 1, default)
    d[6], d[7] = n_st + 1, n_par + 1
    struct.pack_into('<14I', e3, m['off'], *d)
    struct.pack_into('<I', e3, 8, len(e3))
    assert len(e3) <= kp.E3_MAX, f'Entry 3 {len(e3):#x} bytes > {kp.E3_MAX:#x}'
    return bytes(e3), n_par + 1


def patch_teemah_c(l6ia_bytes, freq=400.0, mix=1.0, mode=1, hp_type=4, lp_type=3, sections=2, cost=150, log=print):
    """Test build C: Freq (slot 6), Mix (slot 7, the Line 6 law) and Mode (slot 8, Full / Crossover) on
    build_members_c's composite; every new member a knob writes is registered (Blob.register)."""
    assert FREQ_MIN <= freq <= FREQ_MAX and 0.0 <= mix <= 1.0 and mode in (0, 1)
    e0 = kp.get_entry_swapped(l6ia_bytes, 0)
    e3 = kp.get_entry_swapped(l6ia_bytes, 3)
    e4 = kp.get_entry_swapped(l6ia_bytes, 4)
    m = kp.find_model(kp.parse_entry3_full(e3), MODEL)
    w4 = struct.unpack('<%dI' % (len(e4) // 4), e4)
    b0 = entry4_blob.blob(w4, 0)
    members = build_members_c(b0, freq, hp_type, lp_type, mix, mode, sections)
    b0_new, comp = append_composite(b0, members, n_buf=3)
    blob = Blob(entry4_blob.blob(w4, m['blob_index']))
    blob.renumber(C_SLOT_MAP, comp)
    e3, s_freq = e3_add_param(e3, MODEL, *FREQ_SRC, freq)
    e3, s_mix = e3_add_param(e3, MODEL, *MIX_SRC, mix)
    e3, s_mode = e3_add_list(e3, MODEL, MODE_NAME, MODE_LABELS, mode)
    assert (s_freq, s_mix, s_mode) == (6, 7, 8)
    blob.add_param(s_freq, f2u(freq), [(s, 2, None) for s in C_HP + C_LP + C_HPD])
    blob.add_param(s_mix, f2u(mix), [(C_MIX, 1, ('curve', LINE6_WET)), (C_MIX, 2, ('curve', LINE6_DRY))])
    blob.add_param(s_mode, mode, [(s, 1, ('select', 0, hp_type if k < sections else 0)) for k, s in enumerate(C_HPD)]
                   + [(C_LPMIX, 2, ('select', f2u(OFF_DB), f2u(0.0)))], input_op=patch_comp.INT_INPUT_OP)
    blob.register({s: members[s - 1][3] for s in C_HP + C_LP + C_HPD + (C_MIX, C_LPMIX)})
    assert blob.sub_map() == blob.positions(), 'sub-bindings do not match the node tree'
    blob = blob.assemble()
    replaced = {3: e3, 4: e4_with(e4, b0_new, m['blob_index'], blob)}
    assert not kp.pmap_slots_without_param(replaced[4], m['blob_index'],
                                           kp.find_model(kp.parse_entry3_full(e3), MODEL)['params'])
    e0 = patch_comp.e0_set_param_counts(e0, m['words'][2], 5, s_mode)
    entries = l6ia.parse(l6ia_bytes)
    e2_raw = l6ia_bytes[entries[2]['payload_off']: entries[2]['payload_off'] + entries[2]['payload_len']]
    e2_exp, _ = kp.expand_sharc_blob0_buffer(e2_raw)
    replaced[2] = l6ia.swap32(e2_exp)
    replaced[0], old_cost = patch_gate.e0_add_cost(e0, m['words'][2], cost)
    log(f"[+] {MODEL}: composite {comp:#x} ({len(members)} members, 3 buffers), crossover x{sections} HP type "
        f"{hp_type} LP type {lp_type}; knobs Freq (slot 6, default {freq:g} Hz), Mix (slot 7, default {mix:.0%}), "
        f"Mode (slot 8, {'/'.join(MODE_LABELS)}, default {MODE_LABELS[mode]}); Blob 0 {len(b0):#x} -> "
        f"{len(b0_new):#x} words; DSP cost {old_cost} -> {old_cost + cost}")
    new, _, _ = l6ia.rebuild_archive(l6ia_bytes, replaced)
    return new


def patch_teemah_d(l6ia_bytes, freq=FREQ_MIN, dry_db=LEVEL_MIN, wet_db=0.0, mode=0, hp_type=4, lp_type=3, sections=2,
                   cost=100, log=print):
    """Test build D: test build A's composite (build_members) with four knobs, every member they write registered:

      Full      (mode 0): y = Level * (w * T(HP(x)) + d * x)        (the low-passes type 0)
      Crossover (mode 1): y = Level * (w * T(HP(x)) + d * LP(x))

    slot 6 Frequency (pid 2 of the HPs and LPs), slot 7 Dry Level d (-60..+18 dB) and slot 8 Wet Level w (-60..+9 dB),
    Double Double's records, straight to the mixer's pids 2 and 1, which take dB; slot 9 Mode (int list Full / Xover, op 0x2a
    selects of the LPs' types {0, lp_type}). Level (slot 5) stays the block's last member, after the mixer."""
    assert FREQ_MIN <= freq <= FREQ_MAX and mode in (0, 1)
    assert LEVEL_MIN <= dry_db <= DRY_MAX and LEVEL_MIN <= wet_db <= LEVEL_MAX
    e0 = kp.get_entry_swapped(l6ia_bytes, 0)
    e3 = kp.get_entry_swapped(l6ia_bytes, 3)
    e4 = kp.get_entry_swapped(l6ia_bytes, 4)
    m = kp.find_model(kp.parse_entry3_full(e3), MODEL)
    w4 = struct.unpack('<%dI' % (len(e4) // 4), e4)
    b0 = entry4_blob.blob(w4, 0)
    members = build_members(b0, freq, hp_type, lp_type, dry_db, wet_db, split=not mode, sections=sections)
    assert members[D_MIX - 1][0][6] == 0x84
    b0_new, comp = append_composite(b0, members)
    blob = renumber_blob(entry4_blob.blob(w4, m['blob_index']), comp)
    e3, s_freq = e3_add_param(e3, MODEL, *FREQ_SRC, freq)
    e3, s_dry = e3_add_param(e3, MODEL, *DRY_SRC, dry_db, top=DRY_MAX)
    e3, s_wet = e3_add_param(e3, MODEL, *WET_SRC, wet_db)
    e3, s_mode = e3_add_list(e3, MODEL, MODE_NAME, D_MODE_LABELS, mode)
    assert (s_freq, s_dry, s_wet, s_mode) == (6, 7, 8, 9)
    blob.add_param(s_freq, f2u(freq), [(s, 2, None) for s in FREQ_HP_SLOTS + FREQ_LP_SLOTS])
    blob.add_param(s_dry, f2u(dry_db), [(D_MIX, 2, None)])
    blob.add_param(s_wet, f2u(wet_db), [(D_MIX, 1, None)])
    blob.add_param(s_mode, mode, [(s, 1, ('select', 0, lp_type if k < sections else 0))
                                  for k, s in enumerate(FREQ_LP_SLOTS)], input_op=patch_comp.INT_INPUT_OP)
    blob.register({s: members[s - 1][3] for s in FREQ_HP_SLOTS + FREQ_LP_SLOTS + (D_MIX,)})
    assert blob.sub_map() == blob.positions(), 'sub-bindings do not match the node tree'
    blob = blob.assemble()
    replaced = {3: e3, 4: e4_with(e4, b0_new, m['blob_index'], blob)}
    assert not kp.pmap_slots_without_param(replaced[4], m['blob_index'],
                                           kp.find_model(kp.parse_entry3_full(e3), MODEL)['params'])
    e0 = patch_comp.e0_set_param_counts(e0, m['words'][2], 5, s_mode)
    entries = l6ia.parse(l6ia_bytes)
    e2_raw = l6ia_bytes[entries[2]['payload_off']: entries[2]['payload_off'] + entries[2]['payload_len']]
    e2_exp, _ = kp.expand_sharc_blob0_buffer(e2_raw)
    replaced[2] = l6ia.swap32(e2_exp)
    replaced[0], old_cost = patch_gate.e0_add_cost(e0, m['words'][2], cost)
    log(f"[+] {MODEL}: composite {comp:#x} ({len(members)} members, 2 buffers), crossover x{sections} HP type "
        f"{hp_type} LP type {lp_type}; knobs Freq (slot 6, default {freq:g} Hz), Dry Level (slot 7, {dry_db:g} dB), "
        f"Wet Level (slot 8, {wet_db:g} dB), Mode (slot 9, {'/'.join(D_MODE_LABELS)}, default {D_MODE_LABELS[mode]}); "
        f"Blob 0 {len(b0):#x} -> {len(b0_new):#x} words; DSP cost {old_cost} -> {old_cost + cost}")
    new, _, _ = l6ia.rebuild_archive(l6ia_bytes, replaced)
    return new


def patch_teemah(l6ia_bytes, freq=400.0, hp_type=4, lp_type=3, dry_db=0.0, wet_db=0.0, split=False, cost=100,
                 sections=2, freq_knob=False, bind=True, log=print):
    e0 = kp.get_entry_swapped(l6ia_bytes, 0)
    e3 = kp.get_entry_swapped(l6ia_bytes, 3)
    e4 = kp.get_entry_swapped(l6ia_bytes, 4)
    m = kp.find_model(kp.parse_entry3_full(e3), MODEL)
    w4 = struct.unpack('<%dI' % (len(e4) // 4), e4)
    b0 = entry4_blob.blob(w4, 0)
    members = build_members(b0, freq, hp_type, lp_type, dry_db, wet_db, split, sections)
    b0_new, comp = append_composite(b0, members)
    blob = renumber_blob(entry4_blob.blob(w4, m['blob_index']), comp)
    replaced = {}
    knob = ''
    if freq_knob:
        assert FREQ_MIN <= freq <= FREQ_MAX, f'--freq {freq:g} is outside the knob\'s {FREQ_MIN:g}..{FREQ_MAX:g} Hz'
        e3, slot = e3_add_param(e3, MODEL, *FREQ_SRC, freq)
        targets = FREQ_HP_SLOTS + ((), FREQ_LP_SLOTS)[not split]
        inits = {s: members[s - 1][3] for s in targets} if bind else None
        blob.add_knob(slot, freq, targets, pid=2, bind=inits)
        replaced[3] = e3
        e0 = patch_comp.e0_set_param_counts(e0, m['words'][2], slot - 1, slot)
        knob = (f"; Freq knob slot {slot} ({FREQ_MIN:g}..{FREQ_MAX:g} Hz, default {freq:g}) -> pid 2 of "
                f"{', '.join(f'[1, {s}]' for s in targets)}, {'bound' if bind else 'unbound'}")
    assert blob.sub_map() == {k: v for k, v in blob.positions().items()}, 'sub-bindings do not match the node tree'
    blob = blob.assemble()
    replaced[4] = e4_with(e4, b0_new, m['blob_index'], blob)
    if freq_knob:
        assert not kp.pmap_slots_without_param(replaced[4], m['blob_index'],
                                               kp.find_model(kp.parse_entry3_full(e3), MODEL)['params'])
    entries = l6ia.parse(l6ia_bytes)
    e2_raw = l6ia_bytes[entries[2]['payload_off']: entries[2]['payload_off'] + entries[2]['payload_len']]
    e2_exp, _ = kp.expand_sharc_blob0_buffer(e2_raw)
    replaced[2] = l6ia.swap32(e2_exp)
    replaced[0], old_cost = patch_gate.e0_add_cost(e0, m['words'][2], cost)
    log(f"[+] {MODEL}: composite {comp:#x} ({len(members)} members, 2 buffers), crossover {freq:g} Hz x{sections}, "
        f"HP type {hp_type}, {'split' if split else f'LP type {lp_type}'}, wet {wet_db:g} dB, dry {dry_db:g} dB; "
        f"Blob 0 {len(b0):#x} -> {len(b0_new):#x} words; DSP cost {old_cost} -> {old_cost + cost}{knob}")
    new, _, _ = l6ia.rebuild_archive(l6ia_bytes, replaced)
    return new


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input', help='stock .hxf or L6IA')
    ap.add_argument('output')
    ap.add_argument('--freq', type=float, help='crossover frequency, Hz (default 400; --levels 20)')
    ap.add_argument('--hp-type', type=int, default=4, help='0x102 type of the high-pass (default 4: 2nd order)')
    ap.add_argument('--lp-type', type=int, default=3, help='0x102 type of the low-pass (default 3: 2nd order)')
    ap.add_argument('--dry-db', type=float, help='gain of the dry path at the mixer, dB (default 0, -120: off; '
                                                  f'--levels: Dry Level default, {LEVEL_MIN:g}..{DRY_MAX:g}, default {LEVEL_MIN:g})')
    ap.add_argument('--wet-db', type=float, help='gain of the drive path at the mixer, dB (default 0)')
    ap.add_argument('--split', action='store_true', help='dry path full band (low-passes bypassed)')
    ap.add_argument('--cost', type=int, default=100)
    ap.add_argument('--sections', type=int, choices=(1, 2), default=2,
                    help='filter sections a side (default 2: LR4 with the default types)')
    ap.add_argument('--freq-knob', action='store_true',
                    help=f'test build B: a Freq knob (slot 6, {FREQ_MIN:g}..{FREQ_MAX:g} Hz, default --freq) on the '
                         'crossover filters\' frequency')
    ap.add_argument('--unbound', action='store_true',
                    help='with --freq-knob: writers only, the filters not registered in the node tree')
    ap.add_argument('--knobs', action='store_true',
                    help='test build C: Freq, Mix (Line 6 law) and Mode (Full / Crossover) knobs, slots 6..8')
    ap.add_argument('--mix', type=float, default=1.0, help='with --knobs: Mix default, 0..1 (default 1: all drive)')
    ap.add_argument('--levels', action='store_true',
                    help='test build D: Freq, Dry Level, Wet Level and Mode knobs, slots 6..9, on build A\'s composite')
    ap.add_argument('--mode', choices=MODE_LABELS + D_MODE_LABELS[1:], help='with --knobs / --levels: Mode default (Crossover / Full)')
    ap.add_argument('--donor', help='stock .hxf, when input is a raw L6IA and output a .hxf')
    a = ap.parse_args()
    data, is_hxf = kp.load_l6ia_from_any(a.input)
    if a.levels:
        assert not (a.knobs or a.freq_knob or a.split or a.unbound), '--levels replaces the other builds\' options'
        out = patch_teemah_d(data, FREQ_MIN if a.freq is None else a.freq, LEVEL_MIN if a.dry_db is None else a.dry_db,
                             a.wet_db or 0.0, int((a.mode or 'Full') != 'Full'), a.hp_type, a.lp_type, a.sections,
                             a.cost)
    elif a.knobs:
        assert not (a.freq_knob or a.split or a.unbound), '--knobs replaces --freq-knob, --split and --unbound'
        out = patch_teemah_c(data, a.freq or 400.0, a.mix, MODE_LABELS.index(a.mode or 'Crossover'), a.hp_type,
                             a.lp_type, a.sections, a.cost if a.cost != 100 else 150)
    else:
        out = patch_teemah(data, a.freq or 400.0, a.hp_type, a.lp_type, a.dry_db or 0.0, a.wet_db or 0.0, a.split,
                           a.cost, a.sections,
                           freq_knob=a.freq_knob, bind=not a.unbound)
    kp.save_l6ia_to_any(out, a.output, donor_hxf=a.input if is_hxf else a.donor)
    print(f'[+] Wrote {a.output}')


if __name__ == '__main__':
    main()

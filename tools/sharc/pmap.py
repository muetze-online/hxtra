#!/usr/bin/env python3
"""pmap - a model's knobs traced through its parameter map to the primitives they set.

For each model: its Entry 3 parameters, its signal chain (composite members in order, the
node tree's overrides applied, init values with the blob's init records over the composite's)
and, per writer, the expression that feeds it, back to the knobs. Nested maps (table F, one
per composite member, in member order) are followed: a writer to a composite's path with
pid k feeds slot k of that member's map. Layout and ops: docs/research/sharc/model-blobs.md.

    python3 tools/sharc/pmap.py [--hxf F] MODEL...            text, MODEL = name or display name
    python3 tools/sharc/pmap.py [--hxf F] --category distortion,eq [--sub mono] --json OUT
"""
import argparse
import json
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [p for p in (_HERE, os.path.dirname(_HERE)) if p not in sys.path]

import entry4_blob as e4  # noqa: E402
import kit_patch_amp as kp  # noqa: E402


def fl(v):
    return struct.unpack('<f', struct.pack('<I', v))[0]


def fv(v):
    """A word as a float when it looks like one, else the integer."""
    x = fl(v)
    return round(x, 6) if 1e-6 <= abs(x) < 1e8 else v


class Firmware:
    def __init__(self, path):
        l6, _ = kp.load_l6ia_from_any(path)
        self.e3 = kp.parse_entry3_full(kp.get_entry_swapped(l6, 3))
        e = kp.get_entry_swapped(l6, 4)
        self.w4 = struct.unpack('<%dI' % (len(e) // 4), e)
        self.b0 = e4.blob(self.w4, 0)

    def blob(self, i):
        return e4.blob(self.w4, i)

    def comp(self, cid):
        return e4.parse_composite(self.b0, cid)


def path_at(b, off):
    """A path in a model blob: its length, then one slot per level."""
    return tuple(b[off + 1: off + 1 + b[off]])


def parse_map(b, base):
    """The map whose 24-word header is at pm offset `base` (offsets are pm-relative,
    nested maps included). Header: offsets of tables A..F at words 0, 2, .., 10,
    sizes at 12..17, counts nA..nF at 18..23."""
    po = b[6]
    pm = b[po: po + b[7]]
    h = pm[base: base + 24]
    oA, oB, oC, oD, oF = h[0], h[2], h[4], h[6], h[10]
    nA, nB, nC, nD, _, nF = h[18:24]
    nodes = []
    for k in range(nA):
        n = pm[oA + 10 * k: oA + 10 * k + 10]     # {data_off, 0, id, op, n_out, state, dlen, dlen, is_input, 0}
        nd = {'id': n[2], 'op': n[3], 'state': n[5], 'dlen': n[6], 'input': n[8]}
        if n[3] == 1:
            nd['path'], nd['pid'] = path_at(b, pm[n[0]]), pm[n[0] + 1]
        elif n[6]:
            nd['data'] = [fv(x) for x in pm[n[0]: n[0] + n[6]]]
        nodes.append(nd)
    tables = {}
    for k in range(nB):                             # B: {data_off, 0, node id, 0, count, 0}
        r = pm[oB + 6 * k: oB + 6 * k + 6]
        tables[r[2]] = [fv(x) for x in pm[r[0]: r[0] + r[4]]]
    for nd in nodes:
        if nd['id'] in tables:
            nd['table'] = tables[nd['id']]
    return {'nodes': {n['id']: n for n in nodes},
            'edges': [tuple(pm[oC + 4 * k: oC + 4 * k + 4]) for k in range(nC)],   # {src, dst, src port, dst port}
            'slots': {pm[oD + 4 * k + 2]: pm[oD + 4 * k] for k in range(nD)},       # D: slot -> input node
            'F': [list(pm[oF + 8 * k: oF + 8 * k + 8]) for k in range(nF)]}


def init_records(b):
    """The blob's init records: path -> {pid: value}."""
    out = {}
    for k in range(b[11]):
        soff, pid, voff, _kind = b[b[10] + 4 * k: b[10] + 4 * k + 4]
        out.setdefault(path_at(b, soff), {})[pid] = fv(b[voff + 1])
    return out


def node_tree(b):
    """path -> overriding type id (None where the node keeps the composite's member)."""
    out = {}

    def walk(o, prefix, top):
        slot, t, co, cc = b[o: o + 4]
        p = prefix if top else prefix + (slot,)
        out[p] = None if t == 0xffffffff else t
        for j in range(cc):
            walk(co + 4 * j, p, False)
    walk(b[2], (), True)
    return out


def is_comp(t):
    return 0x600000 <= t < 0x601000


def model_report(fw, m):
    b = fw.blob(m['blob_index'])
    nt = node_tree(b)
    root = nt[()]
    inits = init_records(b)
    params = {p['slot']: p for p in m['params']}

    def member_type(path):
        t = root
        for i, s in enumerate(path):
            t = nt.get(tuple(path[:i + 1])) or fw.comp(t)['members'][s - 1]['type_id']
        return t

    def expr(mp, nid, slotname, memo):
        if nid not in memo:
            nd = mp['nodes'][nid]
            if nd['input']:
                memo[nid] = slotname(next((s for s, n in mp['slots'].items() if n == nid), None))
            else:
                ins = {dp: expr(mp, src, slotname, memo) for src, dst, _, dp in mp['edges'] if dst == nid}
                r = {'op': hex(nd['op']), 'ins': {str(k): v for k, v in sorted(ins.items())}}
                for k in ('table', 'data'):
                    if k in nd:
                        r[k] = nd[k]
                memo[nid] = r
        return memo[nid]

    def walk_map(base, comp_path, slotname):
        mp = parse_map(b, base)
        memo, writes = {}, []
        for w in (n for n in mp['nodes'].values() if n['op'] == 1):
            src = [s for s, d, _, _ in mp['edges'] if d == w['id']]
            writes.append((w['path'], w['pid'], expr(mp, src[0], slotname, memo) if src else None))
        c = fw.comp(member_type(comp_path))
        subs = [k + 1 for k, mm in enumerate(c['members'])
                if is_comp(nt.get(tuple(comp_path) + (k + 1,)) or mm['type_id'])]
        rows = [r for r in mp['F'] if any(r)]
        assert len(rows) == len(subs), f"{m['displayName']}: {len(rows)} nested maps for {len(subs)} composites"
        for s, r in zip(subs, rows):
            p = tuple(comp_path) + (s,)
            feeds = {pid: e for wp, pid, e in writes if wp == p}

            def sn(slot, feeds=feeds, p=p):
                if slot in feeds:
                    return feeds[slot]
                v = inits.get(p, {}).get(slot)
                return {'const': v, 'from': f'init {list(p)} pid {slot}'}
            writes += walk_map(r[0], p, sn)
        return writes

    def top_slot(s):
        p = params.get(s)
        return {'knob': (p['displayName'] or p['name']) if p else None, 'slot': s}

    out = {'name': m['name'], 'display': m['displayName'], 'model': m['model_id'], 'blob': m['blob_index'],
           'root': hex(root),
           'params': [{'slot': p['slot'], 'name': p['name'], 'disp': p['displayName'] or p['name'],
                       'type': p['type'], 'min': round(p['min'], 6), 'max': round(p['max'], 6),
                       'default': round(p['default'], 6),
                       'list': {k: v for k, v in p['meta'].items() if k.isdigit()}} for p in m['params']],
           'chain': [], 'writes': []}
    for path, pid, e in walk_map(0, (), top_slot):
        t = member_type(path) if path else root
        if path and is_comp(t):
            continue                                # fed into that member's nested map
        out['writes'].append({'path': list(path), 'type': hex(t) if path else 'root', 'pid': pid, 'expr': e})

    def chain(t, prefix, depth):
        for k, mm in enumerate(fw.comp(t)['members']):
            p = prefix + (k + 1,)
            tt = nt.get(p) or mm['type_id']
            init = {pp: fv(v) for pp, v in mm['init_params']}
            init.update(inits.get(p, {}))
            kind = {4: 'in-port', 8: 'out-port', 1: 'tap'}.get(mm['flags'], hex(tt))
            out['chain'].append({'path': list(p), 'depth': depth, 'type': kind,
                                 'in': [fw.b0[mm['in_off'] + i] for i in range(mm['n_in'])],
                                 'out': [fw.b0[mm['out_off'] + i] for i in range(mm['n_out'])],
                                 'init': init})
            if mm['flags'] == 0 and is_comp(tt):
                chain(tt, p, depth + 1)
    chain(root, (), 0)
    return out


def show(r):
    print(f"== {r['display']} ({r['name']}, model {r['model']}, blob {r['blob']}, root {r['root']})")
    for p in r['params']:
        rng = p['list'] or f"{p['min']}..{p['max']}"
        print(f"  slot {p['slot']:2d} {p['disp']:14s} type {p['type']} {rng} default {p['default']}")
    for c in r['chain']:
        print('   ' + '  ' * c['depth'], c['path'], c['type'], c['in'], '->', c['out'], c['init'] or '')
    for w in r['writes']:
        print('  W', w['path'], w['type'], 'pid', w['pid'], '=', json.dumps(w['expr']))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--hxf', default=os.environ.get('HX_STOMP_HXF'))
    ap.add_argument('--category', help='comma-separated Entry 3 categories')
    ap.add_argument('--sub', default='mono', help="subcategory with --category ('' for all)")
    ap.add_argument('--json', help='write the reports here instead of printing them')
    ap.add_argument('model', nargs='*')
    a = ap.parse_args(argv)
    if not a.hxf:
        raise SystemExit('no firmware: pass --hxf or set HX_STOMP_HXF')
    fw = Firmware(a.hxf)
    if a.category:
        cats = a.category.split(',')
        ms = [m for m in fw.e3['models'] if m['meta'].get('category') in cats
              and (not a.sub or m['meta'].get('subcategory') == a.sub)]
    else:
        ms = [kp.find_model(fw.e3, q) for q in a.model]
    reports = [model_report(fw, m) for m in ms]
    if a.json:
        json.dump(reports, open(a.json, 'w'), indent=1)
        print(f'{len(reports)} models -> {a.json}')
    else:
        for r in reports:
            show(r)


if __name__ == '__main__':
    main()

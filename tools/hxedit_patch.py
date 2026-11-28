#!/usr/bin/env python3
"""hxedit_patch - teach a copy of HX Edit the knobs an HXtra build adds, so .hlx and .hls exports keep them.

HX Edit turns a preset's blocks (a list of values per block) into named .hlx fields through its own model catalog;
values the catalog has no name for are dropped on export, and on import HX Edit fills a block up to the count its
catalog knows. This tool adds the knobs that an HXtra image gives a model (Entry 3 of the image against Entry 3 of
the stock .hxf) to a COPY of HX Edit, and nothing else:

    plan   <image.hxf> --app <HX Edit.app>             what would be patched, per model (writes nothing)
    patch  <copy.app> <image.hxf> [--from <HX Edit.app>] [--labels own|stock]
    check  <copy.app> <image.hxf>                      check a patched copy against an image (reads only)
    probe  <image.hxf> [--slot 0:2] [--all] [--kind PID=K]  check on the unit that it runs this build (edit buffer only)

  --stock <stock .hxf>  Stock 3.80 of the same unit (default: $HX_STOMP_HXF), the base the knobs are counted from.

The patch must fit the build that runs on the unit exactly: the unit drops a whole preset when a block carries more
values than it knows for the model, and a patched HX Edit sends the patched count with every block of that model.
So patch from the image that is installed (or RAM-run), and `probe` it first: it writes one block per kind of change
into the edit buffer of the current preset, with the patched count, and reads it back.

What the patch changes, in Contents/Resources of the copy (the original app stays untouched; no Line 6 data in here,
everything is read from the user's own files at patch time):
  Helix.sym             per model variant (…Mono/…Stereo) the value names in blob order, the new ones where the
                        image has them (text edit: only those lines change)
  HelixModelDefs.bin    per model the knob definitions (range, default, display type), before @enabled
                        (MessagePack, byte-equal round trip; knobs only the stereo variant has get stereo-only)
  HX_ModelCatalog.bin   the knob list of the edit view, per occurrence (Mono, Stereo, Legacy subcategory)
  HX_ModelCatalog.json  the same as text
  HelixControls.json    display types for list knobs no stock type labels (--labels own; text edit)
  Helix.sym etc.        models only the image has and NEW_MODEL_PLACES names (the Metronome): its entry at its preset id,
                        a definition (mono, the knobs, @enabled), a browser entry (Looper > Mono), refused on a stock id
  hxtra-knobs.json      a note what was patched for which image (makes patch idempotent)
The *.models files are left alone: HX Edit does not load them (no such file name in the program).
Display types come from HX Edit's own catalog: each new knob takes the type that stock knobs with the same firmware
display data have (dB, Hz with Off, seconds, on/off), and a list knob the stock type with the same labels.
--labels own (default) makes a type for list knobs whose labels no stock type has (Full/Xover, meter positions);
--labels stock takes a stock type with as many labels instead (wrong labels, e.g. Off/On for Full/Xover).
Then the copy is signed ad hoc (codesign --force --deep -s -) after removing the quarantine flag.

Never give the patched app to others; patch your own copy. How to use it: docs/hx-edit.md.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

import msgpack

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.environ.get('HXTRA_TOOLS'), _HERE):
    if _p and os.path.exists(os.path.join(_p, 'kit_patch_amp.py')):
        sys.path[:0] = [_p, os.path.join(_p, 'sharc')]
        TOOLS = _p
        break
else:
    TOOLS = None

MARKER = 'hxtra-knobs.json'
HIDE_EXTRA = ()                                     # extras never to add (none so far)
SUFFIXES = ('Mono', 'Stereo')


class PatchError(Exception):
    pass


# ---------------------------------------------------------------- firmware side (Entry 3)

def _fw():
    if TOOLS is None:
        raise PatchError('HXtra tools not found: set HXTRA_TOOLS to the tools/ folder of the hxtra-bass repo')
    import kit_patch_amp as kp
    return kp


def e3_models(hxf):
    """Entry 3 models of an image (.hxf or L6IA): [{name, model_id, words, params: [{name, type, slot, min, max,
    default, displayName, meta}]}], parameters in list order (the order of the values in a preset block)."""
    kp = _fw()
    l6, _ = kp.load_l6ia_from_any(hxf)
    return kp.parse_entry3_full(kp.get_entry_swapped(l6, 3))['models']


def preset_ids(hxf):
    """{Entry 3 name string index: preset id} from the image's Static Model Table (Entry 0, Table 1)."""
    kp = _fw()
    l6, _ = kp.load_l6ia_from_any(hxf)
    e0 = kp.get_entry_swapped(l6, 0)
    segs = kp._parse_e0_segments(e0)
    A = kp.e0_device(segs)
    fo = lambda va, ln=4: kp._e0_vaddr_to_foff(segs, va, ln)
    t1 = struct.unpack_from('<I', e0, fo(A.TAB1_BASE_PTRS[0]))[0]
    _, cnt = kp._decode_thumb_movw(e0[fo(A.TAB1_CNT_INSN): fo(A.TAB1_CNT_INSN) + 4])
    out = {}
    for i in range(cnt):
        name, pid = struct.unpack_from('<IH', e0, fo(t1 + 12 * i, 12))
        out.setdefault(name, pid)
    return out


def bits(f):
    """Integer and bool knobs keep limits and default as bit patterns of a float32 in Entry 3."""
    return struct.unpack('<I', struct.pack('<f', f))[0]


def labels_of(p):
    """The value labels of a list knob (type 2) or an on/off knob (type 1), else None."""
    m = p['meta']
    if p['type'] == 2:                  # labelled from the minimum on, as HX Edit's lists (sync_note: 1 is 1/1)
        keys = sorted((int(k) for k in m if k.isdigit()))
        lo = bits(p['min'])
        return tuple(m[str(k)] for k in keys) if keys and keys == list(range(lo, lo + len(keys))) else None
    if p['type'] == 1 and 'false' in m and 'true' in m:
        return (m['false'], m['true'])
    return None


def value_of(p, which):
    """min, max or default of an Entry 3 knob as HX Edit keeps it: float, int or bool."""
    v = p[which]
    if p['type'] == 2:
        return bits(v)
    if p['type'] == 1:
        return bool(bits(v))
    return v


def added_knobs(stock, image):
    """{model name: (stock params, image params, [index in image params of each new knob])} for every model the
    image gives knobs the stock one lacks. Stock knobs must keep their order (new ones may sit between them, as
    patch_dist puts its knobs before a hidden Smart). Models only the image has are listed under None."""
    st = {m['name']: m for m in stock}
    out, new_models = {}, []
    for m in image:
        s = st.get(m['name'])
        if s is None:
            new_models.append(m['name'])
            continue
        sp, ip = s['params'], m['params']
        if [p['name'] for p in sp] == [p['name'] for p in ip]:
            continue
        j, extra = 0, []
        for i, p in enumerate(ip):
            if j < len(sp) and p['name'] == sp[j]['name']:
                j += 1
            else:
                extra.append(i)
        if j != len(sp):
            raise PatchError(f'{m["name"]}: the image drops or reorders stock knobs; not a knob addition')
        out[m['name']] = (sp, ip, extra)
    out[None] = new_models
    return out


# ---------------------------------------------------------------- HX Edit side (the catalog files)

def z(s):
    """HelixModelDefs.bin and HX_ModelCatalog.bin keep strings NUL-terminated (the NUL counts to the length)."""
    return s.encode() + b'\0'


def s_(x):
    return x[:-1].decode() if isinstance(x, bytes) and x.endswith(b'\0') else x


def unpack(raw):
    o = msgpack.unpackb(raw, raw=True, strict_map_key=False)
    if pack(o) != raw:
        raise PatchError('MessagePack round trip differs: unknown catalog format')
    return o


def pack(o):
    return msgpack.packb(o, use_bin_type=False, use_single_float=True)


SYM_LINE = re.compile(r'("symbol": "((?:[^"\\]|\\.)*)",\n\s*"parameters": \[ )(.*?)( \])')


class Catalog:
    """HX Edit's catalog files in a Resources folder, read so that writing them back changes only what was patched."""

    def __init__(self, res):
        self.res = res
        r = lambda n, b=False: open(os.path.join(res, n), 'rb' if b else 'r', **({} if b else {'encoding': 'utf-8'})).read()
        self.sym_text = r('Helix.sym')
        self.sym = {}
        for mt in SYM_LINE.finditer(self.sym_text):
            names = json.loads('[' + mt.group(3) + ']')
            if ', '.join(json.dumps(n) for n in names) != mt.group(3):
                raise PatchError(f'Helix.sym: line of {mt.group(2)} not in the expected form')
            self.sym[json.loads('"' + mt.group(2) + '"')] = names
        if len(self.sym) != self.sym_text.count('"symbol":'):
            raise PatchError('Helix.sym: not every entry in the expected form')
        self.defs = unpack(r('HelixModelDefs.bin', True))
        self.defs_by_id = {s_(m[z('symbolicID')]): m for m in self.defs}
        self.cat_bin = unpack(r('HX_ModelCatalog.bin', True))
        self.cat_text = r('HX_ModelCatalog.json')
        self.cat_json = json.loads(self.cat_text)
        self.controls_text = r('HelixControls.json')
        self.controls = json.loads(self.controls_text)

    # model variants
    def defs_id(self, symbol):
        """The HelixModelDefs entry of a Helix.sym symbol: itself, or without its Mono/Stereo suffix."""
        if symbol in self.defs_by_id:
            return symbol
        for suf in SUFFIXES:
            if symbol.endswith(suf) and symbol[:-len(suf)] in self.defs_by_id:
                return symbol[:-len(suf)]
        return None

    def def_params(self, did):
        return self.defs_by_id[did][z('params')]

    def def_names(self, did):
        return [s_(p[z('symbolicID')]) for p in self.def_params(did)]

    # the edit view's lists: (name of the (sub)category holding it, model dict) in document order
    @staticmethod
    def occurrences(o, mid, binary):
        K = z if binary else (lambda k: k)
        want = K(mid)
        out = []

        def walk(x, sub):
            if isinstance(x, dict):
                if x.get(K('id')) == want and K('params') in x:
                    out.append((sub, x))
                    return
                if K('models') in x and K('name') in x:
                    sub = s_(x[K('name')])
                for v in x.values():
                    walk(v, sub)
            elif isinstance(x, list):
                for v in x:
                    walk(v, sub)
        walk(o, None)
        return out

    def bin_occ(self, mid):
        return self.occurrences(self.cat_bin, mid, True)

    def json_occ(self, mid):
        return self.occurrences(self.cat_json, mid, False)


# ---------------------------------------------------------------- display types

def e3_sig(p, level):
    m = p['meta']
    base = (p['type'], m.get('displayType'), m.get('units'), m.get('valueMinString'))
    if level == 0:
        return base
    base += (labels_of(p),)
    if level == 1:
        return base
    return base + ((round(value_of(p, 'min'), 4), round(value_of(p, 'max'), 4)) if p['type'] == 3
                   else (value_of(p, 'min'), value_of(p, 'max')))


def learn_display(stock, cat):
    """{(level, Entry 3 signature): {HX Edit displayType: count}} from every stock knob whose name HX Edit's catalog
    has at the same place: what HX Edit shows for knobs the firmware describes alike."""
    table = {}
    for m in stock:
        names = cat.sym.get(m['name'])
        did = cat.defs_id(m['name'])
        if names is None or did is None or len(names) != len(m['params']):
            continue
        dp = {s_(p[z('symbolicID')]): p for p in cat.def_params(did)}
        for p, n in zip(m['params'], names):
            d = dp.get(n)
            if d is None:
                continue
            dt = s_(d.get(z('displayType')))
            for lv in (0, 1, 2):
                c = table.setdefault((lv, e3_sig(p, lv)), {})
                c[dt] = c.get(dt, 0) + 1
    return table


def own_type_name(labels):
    return 'hxtra_' + '_'.join(re.sub(r'[^0-9a-z]+', '', x.lower()) or 'x' for x in labels)


def display_type(p, learned, cat, labels_mode, new_types):
    """HX Edit display type for an Entry 3 knob; may add an own type to new_types."""
    lab = labels_of(p)
    if p['type'] == 2 and lab:
        same = sorted(k for k, v in cat.controls.items() if isinstance(v, dict) and v.get('format') == list(lab))
        for lv in (2, 1):
            c = learned.get((lv, e3_sig(p, lv)))
            if c:
                best = max(c, key=lambda k: (c[k], k is not None, k or ''))
                if best in same:
                    return best
        if same:
            return same[0]
        if labels_mode == 'own':
            name = own_type_name(lab)
            new_types[name] = {'isDiscrete': True, 'controlType': 'segmented', 'format': list(lab)} if len(lab) <= 8 \
                else {'isDiscrete': True, 'format': list(lab)}
            return name
        n = sorted(k for k, v in cat.controls.items() if isinstance(v, dict) and v.get('controlType') == 'segmented'
                   and isinstance(v.get('format'), list) and len(v['format']) == len(lab))
        return 'off_on' if len(lab) == 2 and 'off_on' in cat.controls else (n[0] if n else None)
    for lv in (2, 1, 0):
        c = learned.get((lv, e3_sig(p, lv)))
        if c:
            return max(c, key=lambda k: (c[k], k is not None, k or ''))
    return {1: 'off_on', 3: 'generic_knob'}.get(p['type'])


def def_entry(p, dtype, stereo_only):
    """A HelixModelDefs.bin knob for an Entry 3 knob (keys in the stock order)."""
    vt = {1: 2, 2: 0, 3: 1}[p['type']]
    on_off = p['type'] == 1          # Entry 3 keeps 0 as an on/off knob's limits; HX Edit wants False..True (all 318)
    e = {'name': p['displayName'] or p['name'], 'min': False if on_off else value_of(p, 'min'), 'valueType': vt,
         'symbolicID': p['name']}
    if dtype:
        e['displayType'] = dtype
    e['default'] = value_of(p, 'default')
    e['max'] = True if on_off else value_of(p, 'max')
    if stereo_only:
        e = {'stereo-only': True, **e}
    return e


# ---------------------------------------------------------------- models an HXtra build adds

# Where HX Edit's block browser lists a model only HXtra has (Entry 3 name: category, subcategory), and the stock
# model whose HelixModelDefs entry lends category and DSP load and whose browser entry lends picture and labels.
# Names only: every value comes from the user's files. tools/patch_click.py: the Metronome (Looper > Metronome on
# the unit, a copy of a mono tremolo).
NEW_MODEL_PLACES = {'HD2_ClickMetronomeMono': ('Looper', 'Mono', 'HD2_Tremolo60sBiasTrem')}
SYNC_PAIR = ('TempoSync1', 'SyncSelect1')


def model_pids(hxf, models):
    """{Entry 3 model name: preset id} of an image's models (Table 1 of Entry 0)."""
    ids = preset_ids(hxf)
    return {m['name']: ids[m['words'][2]] for m in models if m['words'][2] in ids}


def sym_entries(cat):
    """Helix.sym's entries in list order (the index is the preset id): [(symbol, names)]."""
    out = [(json.loads('"' + mt.group(2) + '"'), json.loads('[' + mt.group(3) + ']')) for mt in SYM_LINE.finditer(cat.sym_text)]
    if len(out) != len(json.loads(cat.sym_text)):
        raise PatchError('Helix.sym: not every entry in the expected form')
    return out


def _subcategory(cat_obj, cname, sname, binary):
    K = z if binary else (lambda k: k)
    for c in cat_obj[K('categories')]:
        if c.get(K('name')) == K(cname):
            for s in c.get(K('subcategories'), []):
                if s.get(K('name')) == K(sname):
                    return s
    return None


def new_model_plan(m, pid, stock_pids, entries, cat, learned, labels_mode, types):
    """(entry for plan['add'], None) or (None, why not)."""
    place = NEW_MODEL_PLACES.get(m['name'])
    if place is None:
        return None, 'no place in the browser known'
    cname, sname, like = place
    base = next((m['name'][:-len(s)] for s in SUFFIXES if m['name'].endswith(s)), m['name'])
    like_def = cat.defs_by_id.get(like)
    like_occ = cat.json_occ(like)
    if pid is None:
        return None, 'no preset id in the image'
    if pid in stock_pids:
        return None, f'preset id {pid} is a stock model\'s'
    if pid >= len(entries):
        return None, f'preset id {pid} beyond Helix.sym ({len(entries)} entries)'
    if m['name'] in cat.sym or base in cat.defs_by_id or cat.json_occ(base):
        return None, 'HX Edit already has a model of that name'
    if like_def is None or not like_occ:
        return None, f'{like} not in HX Edit'
    if _subcategory(cat.cat_json, cname, sname, False) is None or _subcategory(cat.cat_bin, cname, sname, True) is None:
        return None, f'no browser list {cname} > {sname}'
    like_params = {s_(p[z('symbolicID')]): p for p in like_def[z('params')]}
    knobs = []
    for p in m['params']:
        lp = like_params.get(p['name'])             # a knob both have (the synced pair): its stock name and type
        e = def_entry(p, s_(lp.get(z('displayType'))) if lp else display_type(p, learned, cat, labels_mode, types), False)
        if lp:
            e['name'] = s_(lp[z('name')])
        knobs.append(e)
    enabled = like_params.get('@enabled')
    if enabled is None:
        return None, f'{like} has no @enabled'
    # the browser entry: the knobs in order, a synced pair grouped with the knob it switches (as stock delays)
    like_labels = {}
    for e in like_occ[0][1]['params']:
        for d in (e if isinstance(e, list) else [e]):
            like_labels.update(d)
    names = [p['name'] for p in m['params']]
    synced = all(n in names for n in SYNC_PAIR)
    shown = []
    for p in m['params']:
        if synced and p['name'] in SYNC_PAIR:
            continue
        if synced and p['meta'].get('sync') == '1' and p['meta'].get('coupledIndex'):
            shown.append([{n: like_labels.get(n)} for n in SYNC_PAIR] + [{p['name']: None}])
        else:
            shown.append({p['name']: None})
    entry = {'pid': pid, 'symbol': m['name'], 'names': names, 'did': base, 'place': (cname, sname),
             'def': {'params': knobs, 'name': m.get('displayName') or base, 'symbolicID': base, 'stereo': False,
                     'category': like_def.get(z('category')), 'mono': True, 'load': like_def.get(z('load'))},
             'enabled': enabled, 'replaces': entries[pid][0],
             'model': {'id': base, 'name': m.get('displayName') or base, 'image': like_occ[0][1]['image'], 'params': shown}}
    return entry, None


# ---------------------------------------------------------------- the plan

def make_plan(stock, image, cat, labels_mode='own', pids=None):
    """What to change, as a dict: per Helix.sym symbol its new list, per HelixModelDefs id the knobs to insert and
    where, per catalog id and occurrence the knob names to insert and where; skipped models with the reason.
    pids: (stock preset ids, {image model name: preset id}) to add the models only the image has (NEW_MODEL_PLACES)."""
    add = added_knobs(stock, image)
    learned = learn_display(stock, cat)
    plan = {'sym': {}, 'defs': {}, 'cat': {}, 'types': {}, 'skipped': {}, 'new_models': add.pop(None), 'models': {},
            'add': {}}
    if pids is not None:
        stock_pids, image_pids = pids
        entries = sym_entries(cat)
        by_name = {m['name']: m for m in image}
        for name in list(plan['new_models']):
            if name not in NEW_MODEL_PLACES:
                continue
            e, why = new_model_plan(by_name[name], image_pids.get(name), set(stock_pids), entries, cat, learned,
                                    labels_mode, plan['types'])
            if e:
                plan['add'][name] = e
                plan['new_models'].remove(name)
            else:
                plan['skipped'][name] = why
    by_def = {}
    for name, (sp, ip, extra) in add.items():
        names = cat.sym.get(name)
        did = cat.defs_id(name)
        why = None
        if names is None or did is None:
            why = 'HX Edit does not know the model'
        elif len(names) != len(sp):
            why = f'HX Edit lists {len(names)} values, stock firmware {len(sp)}: positions unclear'
        else:
            new_names = [ip[i]['name'] for i in extra]
            taken = set(names) | set(cat.def_names(did))
            clash = [n for n in new_names if n in taken or new_names.count(n) > 1]
            if clash:
                why = f'name already used: {clash}'
        if why:
            plan['skipped'][name] = why
            continue
        rename = dict(zip([p['name'] for p in sp], names))          # Entry 3 name -> HX Edit's name of a stock knob
        plan['sym'][name] = [ip[i]['name'] if i in extra else rename[ip[i]['name']] for i in range(len(ip))]
        plan['models'][name] = {'stock': len(sp), 'patched': len(ip), 'new': [ip[i]['name'] for i in extra]}
        by_def.setdefault(did, {})[name] = (sp, ip, extra, rename)
    for did, variants in by_def.items():
        lists = {v: [ip[i]['name'] for i in extra] for v, (sp, ip, extra, _) in variants.items()}
        first = next(iter(variants.values()))
        if len({tuple(x) for x in lists.values()}) > 1:
            for v in variants:
                plan['skipped'][v] = f'variants of {did} get different knobs: {lists}'
                del plan['sym'][v], plan['models'][v]
            continue
        sp, ip, extra, rename = first
        dnames = cat.def_names(did)
        all_vars = [s for s in cat.sym if cat.defs_id(s) == did]
        stereo_only = all(v.endswith('Stereo') for v in variants) and any(v.endswith('Mono') for v in all_vars)
        after = set()                                   # HX Edit names of stock knobs after the first new one
        for v, (vsp, vip, vex, vre) in variants.items():
            after |= {vre[p['name']] for i, p in enumerate(vip) if i > vex[0] and i not in vex}
        at = next((k for k, n in enumerate(dnames) if n in after),
                  next((k for k, n in enumerate(dnames) if n.startswith('@')), len(dnames)))
        knobs = [ip[i] for i in extra]
        plan['defs'][did] = {'at': at, 'knobs': [def_entry(p, display_type(p, learned, cat, labels_mode, plan['types']),
                                                           stereo_only) for p in knobs]}
        # the edit view: per occurrence the variant it shows (subcategory Mono / Stereo, else the only one)
        for which, occ in (('bin', cat.bin_occ(did)), ('json', cat.json_occ(did))):
            for k, (sub, m) in enumerate(occ):
                var = did + sub if sub in SUFFIXES and did + sub in all_vars else (
                    all_vars[0] if len(all_vars) == 1 else None)
                if var is None:
                    var = next(iter(variants)) if len(variants) == len(all_vars) else None
                if var not in variants:
                    continue
                cur = [s_(next(iter(e))) for e in m[z('params') if which == 'bin' else 'params'] if isinstance(e, dict)]
                if len(cur) != len(m[z('params') if which == 'bin' else 'params']):
                    plan['skipped'].setdefault(var, 'edit view list is grouped; knobs not shown')
                    continue
                pos = next((j for j, n in enumerate(cur) if n in after), len(cur))
                plan['cat'].setdefault(did, {})[(which, k)] = {'at': pos, 'names': [p['name'] for p in knobs]}
    return plan


# ---------------------------------------------------------------- applying the plan

def apply_plan(plan, cat):
    """New file contents {file name: bytes or str} for the plan; nothing written."""
    out = {}
    # Helix.sym: only the lines of patched symbols change, and the entries of added models (by index: preset id)
    by_pid = {a['pid']: a for a in plan.get('add', {}).values()}
    index = iter(range(1 << 30))

    def sub(mt):
        i = next(index)
        sym = json.loads('"' + mt.group(2) + '"')
        if i in by_pid:
            a = by_pid[i]
            head = mt.group(1)[:mt.start(2) - mt.start(1)] + json.dumps(a['symbol'])[1:-1] + mt.group(1)[mt.end(2) - mt.start(1):]
            return head + ', '.join(json.dumps(n) for n in a['names']) + mt.group(4)
        if sym not in plan['sym']:
            return mt.group(0)
        return mt.group(1) + ', '.join(json.dumps(n) for n in plan['sym'][sym]) + mt.group(4)
    text = SYM_LINE.sub(sub, cat.sym_text)
    syms = json.loads(text)
    for a in by_pid.values():
        if syms[a['pid']] != {'symbol': a['symbol'], 'parameters': a['names']}:
            raise PatchError(f'Helix.sym: entry {a["pid"]} not written as planned')
    out['Helix.sym'] = text
    for a in plan.get('add', {}).values():
        d = a['def']
        cat.defs.append({z('params'): [{z(k): (z(v) if isinstance(v, str) else v) for k, v in e.items()}
                                       for e in d['params']] + [a['enabled']],
                         **{z(k): (z(v) if isinstance(v, str) else v) for k, v in d.items() if k != 'params'}})
        cat.defs_by_id[a['did']] = cat.defs[-1]
        _subcategory(cat.cat_bin, *a['place'], True)[z('models')].append(_zcat(a['model']))
    # HelixModelDefs.bin
    for did, d in plan['defs'].items():
        cat.def_params(did)[d['at']:d['at']] = [{z(k): (z(v) if isinstance(v, str) else v) for k, v in e.items()}
                                                for e in d['knobs']]
    out['HelixModelDefs.bin'] = pack(cat.defs)
    # HX_ModelCatalog.bin
    for did, occ in plan['cat'].items():
        bo = cat.bin_occ(did)
        for (which, k), c in occ.items():
            if which == 'bin':
                bo[k][1][z('params')][c['at']:c['at']] = [{z(n): None} for n in c['names']]
    out['HX_ModelCatalog.bin'] = pack(cat.cat_bin)
    # HX_ModelCatalog.json: the params block after the k-th '"id": "<did>",' of the text, regenerated in place
    text = cat.cat_text
    for did, occ in plan['cat'].items():
        jobs = sorted(((k, c) for (which, k), c in occ.items() if which == 'json'), key=lambda kc: -kc[0])
        heads = [h for h in (mt.start() for mt in re.finditer(re.escape(f'"id": {json.dumps(did)},'), text))
                 if _is_model(text, h)]
        if len(heads) != len(cat.json_occ(did)):
            raise PatchError(f'HX_ModelCatalog.json: {did} occurs {len(heads)} times in the text, '
                             f'{len(cat.json_occ(did))} times as a model')
        for k, c in jobs:
            text = _json_insert(text, heads[k], c)
    want = json.loads(text)
    for a in plan.get('add', {}).values():
        text = _json_append_model(text, *a['place'], a['model'])
        _subcategory(want, *a['place'], False)['models'].append(a['model'])
    if json.loads(text) != want:
        raise PatchError('HX_ModelCatalog.json: added model not where planned')
    out['HX_ModelCatalog.json'] = text
    # HelixControls.json: own display types before the closing brace
    if plan['types']:
        text = cat.controls_text.rstrip()
        if not text.endswith('}'):
            raise PatchError('HelixControls.json: unexpected end')
        add = ''.join(',\n  ' + json.dumps(k) + ': ' + json.dumps(v, indent=2).replace('\n', '\n  ')
                      for k, v in plan['types'].items() if k not in cat.controls)
        text = text[:-1].rstrip() + add + '\n}' + cat.controls_text[len(cat.controls_text.rstrip()):]
        json.loads(text)
        out['HelixControls.json'] = text
    return out


def _zcat(o):
    """A catalog value in HX_ModelCatalog.bin's form: strings NUL-terminated, null stays nil."""
    if isinstance(o, dict):
        return {z(k): _zcat(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_zcat(v) for v in o]
    return z(o) if isinstance(o, str) else o


def _json_close(text, at):
    """Index of the bracket that closes the one at text[at] (strings skipped)."""
    depth, i, in_str = 0, at, False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == '\\':
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in '[{':
            depth += 1
        elif c in ']}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise PatchError('HX_ModelCatalog.json: unbalanced brackets')


def _json_append_model(text, cname, sname, model):
    """The text with model appended to the list cname > sname, laid out as the entry before it."""
    obj = json.loads(text)
    target = _subcategory(obj, cname, sname, False)['models']
    hits = []
    for mt in re.finditer(re.escape(f'"name": {json.dumps(cname)},'), text):
        s = text.find(f'"name": {json.dumps(sname)},', mt.end())
        lb = text.find('[', text.find('"models":', s)) if s >= 0 else -1
        if lb >= 0 and json.loads(text[lb:_json_close(text, lb) + 1]) == target:
            hits.append(lb)
    if len(hits) != 1 or not target:
        raise PatchError(f'HX_ModelCatalog.json: list {cname} > {sname} not found once')
    i, first = hits[0] + 1, None
    while True:                                    # the list's entries, in order: the last one's span
        while text[i] in ' \t\r\n,':
            i += 1
        if text[i] == ']':
            break
        first, last = i, _json_close(text, i)
        i = last + 1
    line0 = text.rfind('\n', 0, first) + 1
    ind = re.match(r'[ \t]*', text[line0:]).group(0)
    inner = re.match(r'[ \t]*', text[text.find('\n', first) + 1:]).group(0)
    step = inner[len(ind):]
    ent = lambda d: '{ ' + ', '.join(f'{json.dumps(k)}: {json.dumps(v)}' for k, v in d.items()) + ' }'
    rows = [ind + step * 2 + ('[ ' + ', '.join(ent(d) for d in e) + ' ]' if isinstance(e, list) else ent(e))
            for e in model['params']]
    body = (ind + '{\n' + ''.join(f'{ind}{step}{json.dumps(k)}: {json.dumps(model[k])},\n' for k in ('id', 'name', 'image'))
            + f'{ind}{step}"params": [\n' + ',\n'.join(rows) + f'\n{ind}{step}]\n{ind}}}')
    return text[:last + 1] + ',\n' + body + text[last + 1:]


def _is_model(text, head):
    """A '"id": …' that opens a model: its "params" come before the next "id"."""
    p = text.find('"params": [', head)
    n = text.find('"id": ', head + 1)
    return p >= 0 and (n < 0 or p < n)


def _json_insert(text, head, c):
    at = text.index('"params": [', head) + len('"params": [')
    end = text.index(']', at)                       # entries are { "X": null } or { "X": "Label" }: no ']' inside
    body = text[at:end]
    lines = [ln for ln in body.split('\n') if ln.strip()]
    ents = json.loads('[' + body + ']')
    indent = lines[0][:len(lines[0]) - len(lines[0].lstrip())]
    close = body[len(body.rstrip()):]               # the line break and indent before ']'
    fmt = lambda e: indent + '{ ' + ', '.join(f'{json.dumps(k)}: {json.dumps(v)}' for k, v in e.items()) + ' }'
    if '\n' + ',\n'.join(fmt(e) for e in ents) + close != body:
        raise PatchError('HX_ModelCatalog.json: params block not in the expected form')
    ents[c['at']:c['at']] = [{n: None} for n in c['names']]
    return text[:at] + '\n' + ',\n'.join(fmt(e) for e in ents) + close + text[end:]


# ---------------------------------------------------------------- the app

def md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def resources(app):
    r = os.path.join(app, 'Contents', 'Resources')
    if not os.path.exists(os.path.join(r, 'HelixModelDefs.bin')):
        raise PatchError(f'{app}: not an HX Edit app (no Contents/Resources/HelixModelDefs.bin)')
    return r


def plan_inputs(stock, image):
    """make_plan's stock models, image models and preset ids, from the two .hxf."""
    sm, im = e3_models(stock), e3_models(image)
    return sm, im, (set(model_pids(stock, sm).values()), model_pids(image, im))


def patch_app(app, image, stock, labels_mode='own', sign=True, log=print):
    """Patch the copy APP for IMAGE. Returns the plan; a second call for the same image changes nothing."""
    res = resources(app)
    note = {'image': os.path.basename(image), 'image_md5': md5(image), 'labels': labels_mode}
    mk = os.path.join(res, MARKER)
    if os.path.exists(mk):
        old = json.load(open(mk))
        if all(old.get(k) == v for k, v in note.items()):
            log(f'{app}: already patched for this image; nothing to do')
            return None
        raise PatchError(f'{app} is patched for {old.get("image")} ({old.get("labels")}): start from an unpatched copy')
    cat = Catalog(res)
    sm, im, pids = plan_inputs(stock, image)
    plan = make_plan(sm, im, cat, labels_mode, pids)
    files = apply_plan(plan, cat)
    for n, data in files.items():
        with open(os.path.join(res, n), 'wb' if isinstance(data, bytes) else 'w',
                  **({} if isinstance(data, bytes) else {'encoding': 'utf-8'})) as f:
            f.write(data)
    note['models'] = plan['models']
    note['added'] = {n: {'preset_id': a['pid'], 'replaces': a['replaces'], 'browser': ' > '.join(a['place'])}
                     for n, a in plan['add'].items()}
    note['skipped'] = plan['skipped']
    note['types'] = sorted(plan['types'])
    with open(mk, 'w') as f:
        json.dump(note, f, indent=1)
    if sign:
        subprocess.run(['xattr', '-dr', 'com.apple.quarantine', app], check=False)
        subprocess.run(['codesign', '--force', '--deep', '-s', '-', app], check=True, capture_output=True)
        subprocess.run(['codesign', '--verify', '--deep', app], check=True, capture_output=True)
    return plan


def report(plan, log=print):
    groups = {}
    for name, m in plan['models'].items():
        groups.setdefault(tuple(m['new']), []).append(f"{name} {m['stock']}->{m['patched']}")
    for new, names in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        log(f'+ {", ".join(new)}  ({len(names)} models): {"; ".join(names)}')
    types = {}
    for d in plan['defs'].values():
        for e in d['knobs']:
            types.setdefault(e['symbolicID'], set()).add(e.get('displayType'))
    log('display types: ' + ', '.join(f'{k} {"/".join(sorted(map(str, v)))}' for k, v in types.items()))
    if plan['types']:
        log('own types: ' + ', '.join(f'{k} {v["format"]}' for k, v in plan['types'].items()))
    for name, why in plan['skipped'].items():
        log(f'! not patched: {name}: {why}')
    for name, a in plan['add'].items():
        log(f'+ model {name} as preset id {a["pid"]} (was {a["replaces"]} in Helix.sym), browser '
            f'{" > ".join(a["place"])}: {", ".join(a["names"])}')
    if plan['new_models']:
        log(f'! models HX Edit has no entry for (not patched): {", ".join(plan["new_models"])}')


def check(app, image, stock):
    """Problems of a patched copy against the image it is meant for (empty list: it fits). Per model the image
    changes: HX Edit names as many values as the image has, the new knobs at the image's places; per model it
    leaves alone: as many as stock. The edit view lists the new knobs in the subcategory of their variant only."""
    cat = Catalog(resources(app))
    st = {m['name']: m for m in e3_models(stock)}
    add = added_knobs(list(st.values()), e3_models(image))
    add.pop(None)
    bad = []
    for name, names in cat.sym.items():
        if name not in st:
            continue
        if name in add:
            sp, ip, extra = add[name]
            if len(names) != len(ip) or any(names[i] != ip[i]['name'] for i in extra):
                bad.append(f'{name}: Helix.sym {names}, image {[p["name"] for p in ip]}')
        elif len(names) != len(st[name]['params']) and any(n not in [p['name'] for p in st[name]['params']]
                                                            for n in names):
            bad.append(f'{name}: Helix.sym has names stock does not: {names}')
    for did in {cat.defs_id(n) for n in add}:
        if did is None:
            continue
        variants = [s for s in cat.sym if cat.defs_id(s) == did]
        for which, occ in (('bin', cat.bin_occ(did)), ('json', cat.json_occ(did))):
            for sub, m in occ:
                shown = [s_(next(iter(e))) for e in m[z('params') if which == 'bin' else 'params'] if isinstance(e, dict)]
                var = did + sub if sub in SUFFIXES and did + sub in variants else (variants[0] if len(variants) == 1 else None)
                if var is None:
                    continue
                want = [add[var][1][i]['name'] for i in add[var][2]] if var in add else []
                if var in add and not all(n in shown for n in want):
                    bad.append(f'{did} ({which}, {sub}): edit view lacks {[n for n in want if n not in shown]}')
                if var not in add and any(n in shown for v in variants if v in add
                                          for n in [add[v][1][i]['name'] for i in add[v][2]]):
                    bad.append(f'{did} ({which}, {sub}): edit view shows knobs {var} does not have')
    # models only the image has: Helix.sym at their preset id, a definition, a browser entry
    image_models = {m['name']: m for m in e3_models(image)}
    pids = model_pids(image, list(image_models.values()))
    entries = sym_entries(cat)
    for name in NEW_MODEL_PLACES:
        m = image_models.get(name)
        if m is None or name in st or pids.get(name) is None:
            continue
        pid, names = pids[name], [p['name'] for p in m['params']]
        base = next((name[:-len(s)] for s in SUFFIXES if name.endswith(s)), name)
        if pid >= len(entries) or entries[pid] != (name, names):
            bad.append(f'{name}: Helix.sym[{pid}] is {entries[pid] if pid < len(entries) else None}, image {names}')
        if cat.defs_id(name) != base or cat.def_names(base)[:len(names)] != names:
            bad.append(f'{name}: no HelixModelDefs entry {base} with its knobs')
        if not cat.json_occ(base) or not cat.bin_occ(base):
            bad.append(f'{name}: not in the block browser')
    return bad


# ---------------------------------------------------------------- probe on the unit

def probe_cases(stock, image, all_models=False, prefer=None):
    """One model per kind of change (the same new knobs, mono or stereo, at the end or before hidden ones), or all:
    [(name, patched values, stock values)], values typed (float, int, bool) and at their defaults. prefer(name):
    within a kind of change, take the first model it accepts (e.g. one whose block kind is known), else the first."""
    add = added_knobs(stock, image)
    add.pop(None)
    groups = {}
    for name, (sp, ip, extra) in add.items():
        key = (tuple(ip[i]['name'] for i in extra), name.endswith('Stereo'), extra[-1] == len(ip) - 1)
        groups.setdefault(key, []).append(name)
    pick = [n for names in groups.values()
            for n in (names if all_models else [next((x for x in names if prefer and prefer(x)), names[0])])]
    return [(n, [value_of(p, 'default') for p in add[n][1]], [value_of(p, 'default') for p in add[n][0]])
            for n in add if n in pick]


def fmt_value(v):
    return 'true' if v is True else 'false' if v is False else str(v) if isinstance(v, int) else repr(float(v))


def factory_blocks(stock, pids):
    """{preset id: (block kind, values)} of the first block of each model in the stock factory presets (hx_ctl
    reads Entry 12); models no factory preset holds are missing (pass their kind with --kind)."""
    import hx_ctl
    out = {}
    for pid in pids:
        k, v = hx_ctl.find_kind_and_params({0: {22: []}}, pid, factory=stock)
        if k is not None:
            out[pid] = (k, v)
    return out


def preset_count(model):
    """Body key 3 of a block (the count the unit checks): Entry 3 presetParamCount, else the number of params.
    It leaves out hidden params a preset does not count (Killer Z, Bronze Master: Smart)."""
    c = model.get('meta', {}).get('presetParamCount')
    return int(c) if c else len(model['params'])


PRESET_HIDDEN_SLOT = 10000      # Entry 3 slot of params a preset does not store (Killer Z, Bronze Master: Smart)


def block_values(values, stock_model, factory_values, model=None):
    """The values list of a block: the Entry 3 values of `model` (default: stock_model) without the params a preset
    does not store (slot >= 10000; HX Edit writes Killer Z as 8 values, seen 2026-11-27), then what the factory
    block carries past the stored stock params (FX Loop: a trailing bool, delays and reverbs: one value more)."""
    model = model or stock_model
    kept = [v for v, p in zip(values, model['params']) if p.get('slot', 0) < PRESET_HIDDEN_SLOT]
    stored = sum(p.get('slot', 0) < PRESET_HIDDEN_SLOT for p in stock_model['params'])
    tail = list(factory_values[stored:]) if factory_values else []
    return kept + tail


def probe(image, stock, slot='0:2', all_models=False, log=print, hx_ctl=None, python=sys.executable, kinds=None):
    """Write each case into the edit buffer (current preset; select an empty test preset first) with the patched
    knobs and read it back; a dropped block is tried once more with the stock knobs, to tell a build without the
    knobs (stock accepted) from a wrong model id or block kind (both dropped). Each try is one write.
    The block is built here (hx_ctl's set_model, key 3 = presetParamCount, factory tail values) and sent with
    hx_ctl load-preset. Per kind of change it takes a model the factory presets hold (or kinds={pid: kind});
    a case whose kind is unknown is reported, not written. Returns [(name, pid, patched count, accepted or None)]."""
    import copy
    import hx_ctl as H
    hx_cli = hx_ctl or os.path.join(TOOLS, 'hx_ctl.py')
    env = dict(os.environ, HX_STOMP_HXF=stock)
    models = {m['name']: m for m in e3_models(image)}
    smodels = {m['name']: m for m in e3_models(stock)}
    pids = preset_ids(image)
    pid_of = {n: pids.get(m['words'][2]) for n, m in models.items()}
    fac = factory_blocks(stock, {p for p in pid_of.values() if p is not None})
    kind = {pid: k for pid, (k, _) in fac.items()}
    by_cat = {}                                  # Entry 3 category -> block kinds of its factory-held models
    for n, m in models.items():
        if pid_of[n] in kind:
            by_cat.setdefault(m.get('meta', {}).get('category'), set()).add(kind[pid_of[n]])
    for n, m in models.items():                  # a model no factory preset holds: its category's only kind
        ks = by_cat.get(m.get('meta', {}).get('category'), set())
        if pid_of[n] is not None and pid_of[n] not in kind and len(ks) == 1:
            kind[pid_of[n]] = next(iter(ks))
    kind.update(kinds or {})
    results = []

    def run(*a):
        return subprocess.run([python, hx_cli, '--timeout', '8', *a], capture_output=True, text=True, env=env)

    with tempfile.TemporaryDirectory() as tmp:
        cur, put_f = os.path.join(tmp, 'cur.json'), os.path.join(tmp, 'put.json')
        g = run('get-preset', '-o', cur)
        if g.returncode != 0:
            raise PatchError(f'get-preset failed: {g.stderr.strip()[-300:]}')
        base = json.load(open(cur))
        magic, sec0 = base.get('magic', 'l6-helix\0'), H.from_json(base['preset'])
        path, pos = H._parse_slot(slot, sec0)

        def put(pid, values, count):
            sec = copy.deepcopy(sec0)
            H.set_model(sec, path, pos, pid, values, kind[pid], True, True, True)
            H.blocks(sec)[H.slot_index(path, pos)][20][11][3] = count
            json.dump({'magic': magic, 'preset': H.to_json(sec)}, open(put_f, 'w'))
            r = run('load-preset', put_f)
            g = run('get-preset', '-o', cur)
            if g.returncode != 0:
                raise PatchError(f'get-preset failed: {g.stderr.strip()[-300:]}')
            body = H.blocks(H.from_json(json.load(open(cur))['preset']))[H.slot_index(path, pos)].get(20)
            if isinstance(body, dict) and body.get(24, {}).get(25) == pid:      # the block at the written slot
                return body[11][4], r
            return None, r

        for name, values, stock_values in probe_cases(e3_models(stock), list(models.values()), all_models,
                                                      prefer=lambda n: pid_of.get(n) in fac):  # factory-held: kind and tail values known
            pid = pid_of.get(name)
            if pid is None:
                log(f'{name}: no preset id in the image (not in Table 1)')
                continue
            if pid not in kind:
                results.append((name, pid, len(values), None))
                log(f'{name} (pid {pid}): NOT TESTED, block kind unknown (no factory preset holds a model of this'
                    f' kind of change): pass --kind {pid}=<kind>')
                continue
            fv = fac.get(pid, (None, None))[1]
            sent = block_values(values, smodels[name], fv, models[name])
            got, r = put(pid, sent, preset_count(models[name]))
            ok = got is not None and len(got) == len(sent) and all(
                (x == y) if isinstance(y, bool) else abs(float(x) - float(y)) < 1e-4 for x, y in zip(got, sent))
            msg = 'accepted' if ok else 'DROPPED' if got is None else f'read back {got}, sent {sent}'
            if not ok:
                ssent = block_values(stock_values, smodels[name], fv, smodels[name])
                g2, _ = put(pid, ssent, preset_count(smodels[name]))
                msg += ('; stock knobs accepted: the unit runs a build without these knobs' if g2 is not None
                        and len(g2) == len(ssent) else '; stock knobs dropped too: model id or block kind wrong')
            results.append((name, pid, len(values), ok))
            log(f'{name} (pid {pid}, kind {kind[pid]}): {len(sent)} values, count {preset_count(models[name])}'
                f' ({len(stock_values)} stock) -> {msg}')
    return results


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--stock', default=os.environ.get('HX_STOMP_HXF'), help='stock 3.80 .hxf of the unit')
    sp = ap.add_subparsers(dest='cmd', required=True)
    a = sp.add_parser('plan', help='what would be patched (writes nothing)')
    a.add_argument('image')
    a.add_argument('--app', required=True, help='an HX Edit app (read only)')
    a.add_argument('--labels', choices=('own', 'stock'), default='own')
    a = sp.add_parser('patch', help='patch a copy of HX Edit')
    a.add_argument('app', help='the copy to patch (made from --from if it does not exist)')
    a.add_argument('image')
    a.add_argument('--from', dest='src', help='HX Edit app to copy first')
    a.add_argument('--labels', choices=('own', 'stock'), default='own')
    a.add_argument('--no-sign', action='store_true')
    a = sp.add_parser('check', help='check a patched copy against an image (reads only)')
    a.add_argument('app')
    a.add_argument('image')
    a = sp.add_parser('probe', help='check on the unit that it runs this build (edit buffer only)')
    a.add_argument('image')
    a.add_argument('--slot', default='0:2')
    a.add_argument('--all', action='store_true', help='every patched model, not one per kind of change')
    a.add_argument('--kind', action='append', default=[], metavar='PID=KIND',
                   help='block kind of a model no factory preset holds (repeatable)')
    args = ap.parse_args(argv)
    if not args.stock:
        ap.error('--stock (or HX_STOMP_HXF) is needed: the stock 3.80 .hxf of the unit')
    try:
        if args.cmd == 'plan':
            cat = Catalog(resources(args.app))
            sm, im, pids = plan_inputs(args.stock, args.image)
            plan = make_plan(sm, im, cat, args.labels, pids)
            report(plan)
        elif args.cmd == 'patch':
            if args.src:
                if os.path.exists(args.app):
                    raise PatchError(f'{args.app} exists; --from makes a new copy')
                subprocess.run(['ditto', args.src, args.app], check=True)
            elif os.path.realpath(args.app) == '/Applications/HX Edit.app':
                raise PatchError('that is the installed HX Edit: patch a copy (--from)')
            plan = patch_app(args.app, args.image, args.stock, args.labels, sign=not args.no_sign)
            if plan:
                report(plan)
                print(f'patched{"" if args.no_sign else " and signed"}: {args.app}')
        elif args.cmd == 'check':
            bad = check(args.app, args.image, args.stock)
            for b in bad:
                print('! ' + b)
            print(f'{args.app}: ' + ('fits the image' if not bad else f'{len(bad)} problems'))
            return 1 if bad else 0
        else:
            kinds = {int(a): int(b) for a, b in (kv.split('=') for kv in args.kind)}
            res = probe(args.image, args.stock, args.slot, args.all, kinds=kinds)
            bad = [r for r in res if r[3] is False]
            untested = [r for r in res if r[3] is None]
            print(f'{len(res) - len(bad) - len(untested)} of {len(res)} accepted'
                  + (f', {len(untested)} not tested' if untested else '') + ('' if not bad else ': do not use this patch'))
            return 1 if bad or untested else 0
    except PatchError as e:
        print(f'hxedit_patch: {e}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())

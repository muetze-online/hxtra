#!/usr/bin/env python3
"""e4_compact.py - Entry 4 laid out again without gaps, the last step that touches it (build.py).

Every step that changes Entry 4 writes the blobs it grows past the end or behind a relocated directory and leaves the old
copies where they were: a full HXtra image carries some 2 MiB of dead words, most of it the stock directory and Blob 0.
The M4 reads Entry 4 blob by blob and never its length (docs/research/formats/containers.md), so the dead words cost
only room in the partition (kp.E4_MAX). The image ends with Entry 4 as stock has it: the 6-word header, the directory at word 6, then every blob in index order, each word for word.
Blobs hold no Entry 4 offsets (their relocations are blob-relative, docs/research/sharc/model-blobs.md); earlier steps
move them already. On a compact Entry 4, stock's among them, this changes nothing.
"""
import argparse
import struct

import kit_patch_amp as kp
import l6ia

MAGIC = 0x28267e5e


def compact(e4):
    """Entry 4 (little-endian words) with the directory at word 6 and the blobs after it in index order, no gaps."""
    w = struct.unpack('<%dI' % (len(e4) // 4), e4)
    assert w[0] == MAGIC and w[1] == 4 and w[5] == len(w), f'bad Entry 4 header: {w[:6]}'
    n, d = w[2], w[3]
    pairs = [(w[d + 2 * k], w[d + 2 * k + 1]) for k in range(n)]
    assert w[4] == pairs[0][1], f'header word 4 {w[4]:#x} is not Blob 0 at {pairs[0][1]:#x}'
    assert all(o + ln <= len(w) for ln, o in pairs), 'a blob reaches past the end of Entry 4'
    out = [MAGIC, 4, n, 6, 0, 0] + [0] * (2 * n)
    for k, (ln, o) in enumerate(pairs):
        out[6 + 2 * k: 8 + 2 * k] = [ln, len(out)]
        out.extend(w[o: o + ln])
    out[4], out[5] = out[7], len(out)
    b = struct.pack('<%dI' % len(out), *out)
    if len(b) > kp.E4_MAX:
        raise SystemExit(f'Entry 4 would be {len(b):#x} bytes; its partition holds {kp.E4_MAX:#x}')
    return b


def patch_e4(l6ia_bytes, log=print):
    e4 = kp.get_entry_swapped(l6ia_bytes, 4)
    new = compact(e4)
    log(f'[+] Entry 4 compacted: {len(e4):#x} -> {len(new):#x} bytes ({(len(e4) - len(new)) // 4} dead words dropped, '
        f'limit {kp.E4_MAX:#x})')
    if new == e4:
        return l6ia_bytes
    out, _, _ = l6ia.rebuild_archive(l6ia_bytes, {4: new})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input', help='.hxf or L6IA')
    ap.add_argument('output')
    ap.add_argument('--donor', help='Stock .hxf of the unit, when input is a raw L6IA and output a .hxf')
    a = ap.parse_args()
    data, is_hxf = kp.load_l6ia_from_any(a.input)
    kp.save_l6ia_to_any(patch_e4(data), a.output, donor_hxf=a.input if is_hxf else a.donor)
    print(f'[+] Wrote {a.output}')


if __name__ == '__main__':
    main()

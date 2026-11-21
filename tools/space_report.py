#!/usr/bin/env python3
"""space_report.py - how much of each size-limited part a finished image uses (build.py prints it after e4_compact).

The limits and how sure each is (docs/resource-limits.md):
  Blob 0    the SHARC's Blob 0 buffer, kp.SHARC_BLOB0_MAX_WORDS words [verified offline]
  Entry 3   the largest Entry 3 an HX Stomp is known to load amps from, build.py's E3_MAX; past 0xf3888 it drops every
            amp and preamp [verified on unit]; the partition (kp.E3_MAX) is larger and no limit of the unit's
  Entry 4   its partition, kp.E4_MAX bytes; the M4 reads it blob by blob and never its length [verified offline]
Run it on a compacted image: Entry 4's dead copies (e4_compact.py) would count as used.
"""
import argparse
import struct

import kit_patch_amp as kp


def rows(l6ia_bytes, e3_max):
    """(name, used, limit, unit) for Blob 0, Entry 3 and Entry 4 of an L6IA image."""
    e3 = kp.get_entry_swapped(l6ia_bytes, 3)
    e4 = kp.get_entry_swapped(l6ia_bytes, 4)
    w = struct.unpack_from('<6I', e4, 0)
    b0_words = struct.unpack_from('<I', e4, 4 * w[3])[0]          # directory entry 0: (length, offset) of Blob 0
    return [('Blob 0', b0_words, kp.SHARC_BLOB0_MAX_WORDS, 'words'),
            ('Entry 3', len(e3), e3_max, 'bytes'),
            ('Entry 4', len(e4), kp.E4_MAX, 'bytes')]


def report(l6ia_bytes, e3_max):
    """The rows as text, one line each: used, limit, free and the share used."""
    return '\n'.join(f'[=] {name}: {used:,} of {limit:,} {unit} ({100 * used / limit:.1f} %), {limit - used:,} free'
                     for name, used, limit, unit in rows(l6ia_bytes, e3_max))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input', help='.hxf or L6IA')
    ap.add_argument('--e3-max', type=lambda s: int(s, 0), required=True, help="build.py's E3_MAX")
    a = ap.parse_args()
    data, _ = kp.load_l6ia_from_any(a.input)
    print(report(data, a.e3_max))


if __name__ == '__main__':
    main()

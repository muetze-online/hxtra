# Teemah! (overdrive)

The Teemah! model, its node tree and its stock primitives, as groundwork for a Blend/crossover extension (fork muetze-online/hxtra, branch `bass`). Status tags as in [conventions.md](../../conventions.md).

## Model [verified offline]

- `HD2_DistTeemahMono`, display name "Teemah!", Entry 3 model 92, Entry 4 blob 92 (1472 words), composite `0x6000cb`. Read with `tools/kit_patch_amp.py inspect <hxf> Teemah`.
- Parameters: slot 1 Gain (float, default 0.52), 2 Bass ("Bass Cut", 0.5), 3 Treble ("TrebleCut", 0.5), 4 Clipping (list Up/Center/Down, 0), 5 Level (0.63).

## Composite `0x6000cb` (mono, buffers as `in → out`) [verified offline]

| Path | Type | Buffers | Init |
|---|---|---|---|
| [1] | in-port | → 0 | |
| [2] | `0x102` | 0 → 0 | type 2, 15.6 Hz, 0, Q 0.707 |
| [3] | `0x6000c2` (drive) | 0 → 0 | |
| [4] | `0x102` | 0 → 0 | none (set from the parameter map) |
| [5] | `0x102` | 0 → 0 | none |
| [6] | `0x80` (Level) | 0 → 0 | 0, 0 |
| [7] | out-port | 0 → | |

Drive `0x6000c2`: `0x103` (0 → 1, nine init words) puts a second signal into buffer 1; three `0x102` (type 0, 1000 Hz, Q 0.707), `0x80`, `0x832` (clipper, 32 DM words), `0x225`, then `0x89` mixes [0, 1] into 0 (pid 1 = −7 dB, pid 2 = 0). So the stock block already sums a second path into the clipped one.

## Parameter map and blob init (blob 92) [verified offline]

Paths as `[1, k]` = top-level slot k (table above: k = 2 is the first `0x102`), `[2, 3, k]` = slot k inside the drive.
- Gain (slot 1) → writer `[1, 3]` pid 1 (drive), no curve node. Bass (2) → `[1, 3]` pid 2. Clipping (4, int) → `[1, 3]` pid 3. Treble (3) → an op `0x3` node → `[1, 4]` pid 2, the frequency of the filter after the drive. Level (5) → an op `0x3` node → `[1, 6]` pid 1 (`0x80` gain).
- The blob's init records override the composite's: `[1, 2]` type 2, 6 Hz, Q 0.707; `[1, 4]` type 1, 723.43 Hz; `[1, 5]` type 2, 15.92 Hz; `[1, 6]` −60 dB until Level writes; drive `[1, 3]` pids 1..3 = 0.264, 0, 0.
- Inside the drive: `[2, 3, 3]` type 2, 50 Hz; `[2, 3, 4]` type 5, 18 kHz; `[2, 3, 5]` type 1, 1000 Hz; `[2, 3, 7]` `0x832` pids 2..5 = −7.5, −13.3, −7.5, −13.3 (two threshold pairs), 7 = 20, 8 = 10 000, 14 = 60; `[2, 3, 9]` `0x89` pid 1 = −7 dB (clipped path), pid 2 = 0 dB (buffer 1 from `0x103`).
- **The drive's pids 1..3 reach its members through the drive's nested map** (table F, [model-blobs.md](../model-blobs.md#map-tables-b-and-f-curves-and-nested-maps-verified-offline)) [verified offline, `tools/sharc/pmap.py Teemah`]. Values as 9-point curves over knob 0..1 (op `0x3`), first → last point:
  - Gain → `[2, 3, 6]` `0x80` 0 → 48.75 dB (pre-clipper gain) and `[2, 3, 5]` `0x102` frequency 22 000 → 1700 Hz.
  - Bass Cut → `[2, 3, 4]` `0x102` (type 5) frequency 2900 → 1320 Hz with gain 0 → −23.8 dB, and `[2, 3, 3]` `0x102` (type 2) 46.4 → 3 Hz.
  - Clipping → `[2, 3, 7]` `0x832` pids 3 and 5 through op `0x43` (== 2, == 1) and `0x2b` with the constants −11.1 / −17.4.
  - Treble Cut → `[1, 4]` frequency 10 610 → 309 Hz: the knob lowers the cutoff. Level → `[1, 6]` −60 → +9 dB (0 dB at 0.625).

## Stock primitives for a crossover

- `0x102` biquad: pid 1 type, 2 frequency (Hz), 3 gain (dB), 4 Q; 9 DM words. Types decoded in [filters.md](filters.md) [verified offline]: 1 / 2 = 1st-order low-pass / high-pass (6 dB/oct), 3 / 4 = 2nd-order low-pass / high-pass (12 dB/oct), 5 / 6 = 1st-order low / high shelf, 7, 10, 12 = peaks, 13 / 14 = 2nd-order shelves. Init values across all 454 composites: type 0 (pass-through) ×1236, mostly placeholders set later by the map; 2 ×78 (1–300 Hz), 3 ×50 (800–18 000 Hz), 4 ×146 (3–40 Hz), 5 ×64.
- An earlier version of this note gave `0x60ad61` and the jump table at `0xc72f9` as `0x102`'s coefficient code; they are `0x11c`'s. `0x102`'s is `0x60a603` with the table at `0xc72dc` ([filters.md](filters.md)). So for a crossover from `0x102`: a 1st-order pair is HP type 2 + LP type 1; an LR4 pair (as the Split crossover) two sections each of type 4 and type 3, Q 0.707.
- `0x84` and `0x89` share one registry body (init `0x610224`, control `0x61017a`, process `0x8da28`, DM 2, PM 2): the same mixer under two IDs [verified offline].
- `0x84`, `0x89`: two-input mixers, pid 1 and 2 = gain of each input in dB (common pairs −3/−3, −6/−6, 0/−120). The Ampeg Opto Comp's Blend drives `0x84` pids 1 and 2 from one knob ([compressors.md](compressors.md)), the model for a Blend knob.

## Implications for Blend / Freq / Split-Crossover

- With x the input, b the blend, LR4 = two cascaded 2nd-order Butterworth sections (Q 0.707) per side: crossover y = T(HP(x)) + b·LP(x); split y = T(HP(x)) + b·x.
- **Composite route:** a copy of `0x6000cb` with a tap of x into a spare buffer, two `0x102` high-passes before [3], two `0x102` low-passes on the tap, a mixer at the end. Only stock primitives, no SHARC code. Risk from [gates.md](gates.md): members inserted into a composite run, but a block went dead whenever its blob got a sub-binding for a new member. Freq and Blend need exactly such bindings. Test builds A and B work on the unit with correct positions (below).
- **Wrapper route:** SHARC code around a member, as `src/sharc/frame_probe.asm` and `src/sharc/fx_gate.asm` do. Works for the gate on the unit, but `0x102` and `0x80` are shared by most blocks, so the wrapper would have to recognise Teemah! instances, and the dry signal has to survive from [2] to [6].

## Test build A: `tools/patch_teemah.py` [verified on unit, 2026-11-19]

- A new composite (`0x6001c6` on stock) appended to Blob 0: Teemah!'s members plus a tap (type 0, flag 1, buffer 0 → 1: the stock copy node, 3048 members across stock composites), two `0x102` high-pass sections before the input filter, two `0x102` low-pass sections on buffer 1, a `0x84` mixer [0, 1] → 0 **before** Level (so Level sets the whole output). Values come from the composite's init records; no new bindings in the model blob, whose root and member paths are renumbered (old slots 2..6 → 5..8, 12).
- **Fixed 2026-11-19:** the first A builds (MD5 `2060cecd…`, `d7ba6e56…`) renumbered path words in place, and four of them are also the sub-bindings' node-tree positions ([model-blobs.md](../model-blobs.md#sub-bindings-and-table-f-verified-offline)): Level, the input filter, Treble and the 15.9 Hz filter pointed at positions 5..8 of a root with five children. Now the blob is rebuilt (`patch_teemah.Blob`), a shared word gets its own copy for the position, and the sub-bindings match the node tree as in all 863 stock blobs. Rebuilt: crossover `43bcb8ee…`, split `2e647317…`.
- Default: a 4th-order Linkwitz-Riley crossover as the stock Split crossover builds it ([filters.md](filters.md)): high-pass type 4 and low-pass type 3, two sections each, Q 0.7071; −6.02 dB a band at f, 24 dB/oct, the bands summing to an all-pass (flat). `--sections 1 --hp-type 2 --lp-type 1` gives a 1st-order crossover, which sums flat too (1/(1+s) + s/(1+s) = 1); two 1st-order sections a side (LR2) would cancel at f, (1+s²)/(1+s)².
- The first builds (types 3 and 1, before filters.md) put low-passes on both paths: type 3 is a 2nd-order low-pass, not a high-pass. Replaced.
- Fixed values in the test builds: crossover 400 Hz, wet and dry 0 dB, DSP cost +100 (a guess). `hxf_ram.py plan` passes; Entry 4 grows from 0xbfe3f8 to 0xde20a0 bytes (Blob 0 moved to the tail, as add-model does).
- Tests: `tests/test_teemah_xover.py` (firmware).
- On the unit (RAM run on stock 3.80, bass, by ear; no measurement yet): both builds play, Gain distorts the highs while the low strings stay clean, Level sets the whole output including the dry part. Split's dry part sounds fuller and reaches into the highs. No clicks, no hangs.

## Test build B: a Freq knob, `--freq-knob` [verified on unit, 2026-11-19]

- Entry 3: slot 6 "Frequency", a copy of the Auto Filter's record (float 20..1000 Hz, `displayType` `hz`, short name "Freq"; strings and meta shared, so the string section stays put), default `--freq` (400 Hz). Entry 0: the UI row counts 6.
- Blob 92: a float input node (op `0xa`) for slot 6, a root default, and four writers straight from it, pid 2 (frequency) of `[1, 3]`, `[1, 4]` (high-passes) and `[1, 9]`, `[1, 10]` (low-passes); no curve, as the Parametric's Low Cut drives its `0x102`s in Hz. `pmap.py --sweep`: 20 / 265 / 510 / 755 / 1000 Hz on all four. The blob grows and moves to Entry 4's tail.
- Two builds, because every stock writer (16 564 of 16 566, the exceptions in the Loopers) goes to a member the blob registers (a root child in the node tree with a sub-binding), and the FX-gate builds went dead when a new member was registered ([gates.md](gates.md)):
  - **bound** (default, `teemah_lr4_crossover_freqknob.hxf`, MD5 `9379327e…`): the four filters registered as stock members are: root children 6..9, a sub-binding each to its position, table F 9 rows, init records pids 1..4.
  - **unbound** (`--unbound`, `…_freqknob_unbound.hxf`, MD5 `f0ab4777…`): writers only.
- On the unit (RAM run on stock 3.80, bass, by ear): both builds play, the knob shows as "Freq" in Hz from 400, turning it moves the split (20 Hz: almost all distorted; 1000 Hz: a large clean part), no clicks, and the value survives saving and switching presets.
- So new members can be registered once the node-tree positions and table F are right, and writers to unregistered members work too. Blend and Mode follow the bound build, as stock blocks do.

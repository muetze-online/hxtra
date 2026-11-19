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

## Stock primitives for a crossover

- `0x102` biquad: pid 1 type, 2 frequency (Hz), 3 gain (dB), 4 Q; 9 DM words. Init values across all 454 composites: type 0 ×1236 (mostly placeholders 1000 Hz / Q 0.1, set later by the map), 2 ×78 (1–300 Hz), 3 ×50 (800–18 000 Hz), 4 ×146 (3–40 Hz), 5 ×64 (with gain), 6, 7, 10 with gain. **[hypothesis]** 2 = 2nd-order high-pass, 3 = 2nd-order low-pass, 4 = 1st-order high-pass, 5–7, 10 = shelves/peak. To be confirmed from its control function or in `ssim.py`.
- `0x84`, `0x89`: two-input mixers, pid 1 and 2 = gain of each input in dB (common pairs −3/−3, −6/−6, 0/−120). The Ampeg Opto Comp's Blend drives `0x84` pids 1 and 2 from one knob ([compressors.md](compressors.md)), the model for a Blend knob.

## Implications for Blend / Freq / Split-Crossover

- With x the input, b the blend, LR4 = two cascaded 2nd-order Butterworth sections (Q 0.707) per side: crossover y = T(HP(x)) + b·LP(x); split y = T(HP(x)) + b·x.
- **Composite route:** a copy of `0x6000cb` with a tap of x into a spare buffer, two `0x102` high-passes before [3], two `0x102` low-passes on the tap, a mixer at the end. Only stock primitives, no SHARC code. Risk from [gates.md](gates.md): members inserted into a composite run, but a block went dead whenever its blob got a sub-binding for a new member. Freq and Blend need exactly such bindings. **Untested.**
- **Wrapper route:** SHARC code around a member, as `src/sharc/frame_probe.asm` and `src/sharc/fx_gate.asm` do. Works for the gate on the unit, but `0x102` and `0x80` are shared by most blocks, so the wrapper would have to recognise Teemah! instances, and the dry signal has to survive from [2] to [6].

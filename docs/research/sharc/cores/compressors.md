# Compressor cores

The compressor blocks' cores (primitives `0x271a`, `0x2719` and `0x2710`): which block runs which, their wiring and pids, and what the Deluxe core computes. Status tags as in [conventions.md](../../conventions.md).

## The Deluxe Comp and its core `0x271a` [verified offline]

- Model `HD2_CompressorDeluxeCompMono`, composite `0x6000c9`, members: 0 in-port, 1 `0x239d`, 2 and 3 `0x102`, 4 `0x271a` (compressor core, 2 inputs / 1 output), 5 `0x89` (2 inputs), 6 `0x80`, 7 out-port.
- Registry entry for the core at `0x200320`: `[0x271a, 0x6100f8, 0, 0, 0x6100bd, 0x8d9c9, 0xb, 5]`; process code at PM `0x8d9c9`, which uses `MANT` (log-domain gain).
- The wrapper was called with `I1=0x967de`, `I2=0x967ee` (adjacent 16-word buffers), `I3=0xb2e30`, `I0=0x96fc9`, `I4=0xe14ee`, `I5=0xc5b52`. Sample magnitudes were tiny (~1e-5) in a probably silent capture, so **which buffer is the compressor's input and which its output is unknown.**
- Blocks are 16 samples.
- **Its buffers** [verified on unit, Split image in the [GR meter log](../../features/gr-meter-log.md)]: `I1` is the core's input, which the core does not write; `I2` after the stock code is the compressed audio; `I3` stays near zero.
- **Its settings** [verified on unit, Latching-map and Value-readout images]: the core's `I0` points at its settings: word 0 Threshold, 1 Ratio, 2 Attack, 3 Release. Attack and Release are per-sample one-pole coefficients c = exp(-2 pi / (48000 t)), t the knob time; the time constant is t / (2 pi).
- **The Deluxe core's live gain** [verified offline, by tracing `0x8d9c9` in `ssim`]: the caller passes `I8` = the core's 5 pm words (registry pm 5), read and written as `PM(k, I8)`: word 0 is read and rewritten each sample (envelope), word 1 written each sample (1.0 when nothing is compressed; **[hypothesis]** the linear gain applied), word 3 written each sample, word 4 counts blocks. `I1`/`I2` are circular (`L1` 32, `L2` 16, `B1`/`B2` from the caller); `I1` is advanced to the second input. None of these were in the 32 copied state words, hence no live envelope there. `ssim` now models `MANT` and the multiplier's `Rn = Rx * Ry (SSF/SSFR)`; with guessed state values the simulated output stayed 0, so the state's meaning is not settled.
- **The Deluxe core's loop, disassembled** [verified offline; `ssim` traces with a throwaway disassembler built on `sasm`'s tables]: per sample, `env = c·env + (1−c)·|sidechain × smoothed st[6]|` (c = st[2] Attack while rising, else st[3] Release) in `PM(0,I8)`; `over = log2(env) − st[0]` (threshold in log2 units); gain exponent 0 below the knee, `st[1]·(over + st[9])²·st[10]` in it, `st[1]·over` above (st[1] = 1/ratio − 1, st[9] = half the knee width, st[10] = 1/(2 × width), matching the unit's 0.4983 and 0.5017 at Knee 6 dB); **`PM(1,I8)` = 2^exponent smoothed by st[4], the linear gain**; output = audio × gain × smoothed st[5] × smoothed st[6] (`PM(2,I8)`, `PM(3,I8)`); `PM(4,I8)` counts blocks to st[8]. With the user's playing settings (−28.5 dB, 6:1, 38/300 ms, Knee 6), a decaying pluck gives the same GR from peak comparison and from the gain within 0.5 dB, except the first 21 ms window (peak-compare 0): about 11–12 dB at −12 dBFS peaks, 5 dB at −20 dBFS.
- **The Deluxe loop's static curve and timing** [verified offline, 2026-11-22: `0x8d9c9` run in `ssim` with state words built from the pids, constant-amplitude 1 kHz squares; not yet measured on the unit]: a **feed-forward** compressor. The first input is the audio (`I3` = `I1` before `MODIFY(I1,16)`), the second the sidechain (`I1` + 16, circular `L1` 32). The simulated gain matches gain dB = (1/R − 1)·(L − T) above the knee and (1/R − 1)·(L − T + W/2)²/(2W) inside it (W = pid 8 in dB) within 0.03 dB for all 12 cores' default settings (Deluxe, Rochester, the three 3-Band bands, the seven `0x456` models), with pid 6 shifting the effective threshold by −pid 6. Deluxe defaults (−37.1 dB, 2:1, Knee 6): −3.56 / −8.54 / −18.54 dB at −30 / −20 / 0 dBFS. The release is almost linear in dB: the envelope falls by 54.6/t dB/s (t = Release in s), the reduction by (1 − 1/R) of that (Deluxe 200 ms, 2:1: −13.5 dB recovers in about 100 ms); attack approaches its final value exponentially with τ = t/(2π).
- **Corrections from the loop listing** [verified offline]: `PM(1,I8)` stores the *unsmoothed* new gain (`0x8da07`), so pid 9 (st[4]) mixes the new with the previous sample's gain, out = (1 − c)·new + c·previous (c 0.92 at 1.7 ms), in effect a one-sample delay rather than a smoothing pole. Output gain st[5] and input gain st[6] glide with a fixed coefficient 0.99760 per sample (τ 8.7 ms, `0x8d9d3`). The init `0x6100f8` writes st[9] = st[10] = 0, so a core without a Knee value (all `0x456` models) has a hard knee; pid 8 = 0 also gives a hard knee (the control skips the division, `0x610011`).
- **The Deluxe sidechain** [verified offline, chain of model 68]: the core reads the audio from buffer 0 and the detector from buffer 1, which runs through `0x239d` and a 2nd-order high-pass at 80 Hz (Q 0.707): the detector sees 41.2 Hz 11.8 dB and 30.9 Hz 16.6 dB lower than the audio (filter formula).

## The LA Studio Comp and its core `0x2719`

- **LA Studio Comp** [verified offline]: blobs 66 (mono, composite `0x6000ca`) and 350 (stereo, `0x60001d`). Its compressor core is primitive `0x2719` (registry `0x200188`: control `0x60cc71`, process `0x8d342`, dm 17, pm 5; 2 inputs, 1 output, the Deluxe core `0x271a`'s neighbour), member 15 (path [16]) in mono, members 25 and 39 (paths [26], [40]) in stereo. The core takes pids 1..14; only Type reaches it (pids 1 and 8). Peak Reduction is a gain stage on the core's sidechain input, not on the audio ([10], `0x80` on buffer 2: −120/−38/−26/−15/−5 dB at 0/¼/½/¾/1; corrected 2026-11-22 from a `pmap --sweep`), so, as on the original, it sets the detector level against a fixed threshold; Gain is one after the core ([17]), Mix `0x89` ([23]), Level [24]. So the core's own input and output give its gain reduction without the Gain knob.

## The Kinky Comp, Opto and Red Squeeze: core `0x2710`

- **Kinky Comp** [verified offline]: blobs 149 (mono) and 444 (stereo) run core `0x2710`, not `0x2719` (the node tree overrides the slot; mono member 13, stereo members 22 and 32; corrected 2026-11-22, the knobs' pids 2 and 3 match `0x2710`'s control function). Attack and Release go to the core as pids 2 and 3; Sensitivity drives two `0x80` gain stages, Mix `0x89`, Level `0x80`.
- **Ampeg Opto Comp** [verified offline]: models 216 (mono, composite `0x60014c`) and 493 (stereo, `0x6000ad`); knobs Compression, Release, Blend, Level. The node tree overrides slot 8 to `0x2710` (stereo slots 8 and 20) and slot 4 to `0x2711`, so its core is the Kinky Comp's primitive and the wrapper already runs it. Mono chain: input → `0x80` gain (Compression, pid 1) → buffer 1; the sidechain is filtered from 1 into 2 (`0x81c`, `0x2711`, `0x102`, `0x239d`, another Compression `0x80`); the core runs on [1, 2] and writes over 1; a tap copies the dry input to 2; `0x84` (Blend, pids 1 and 2) mixes [1, 2] into 1; `0x80` Level. **Correction 2026-11-22** (`pmap --sweep`): Compression acts almost only on the sidechain (member 7, `0x80`: −40/−9.5/−4.6/−2.25/0 dB at 0/¼/½/¾/1); the audio-path gain (member 2) moves only 0 … −0.35 dB. The earlier reading follows: so Compression is an input gain into a fixed −18 dB threshold (core init: pid 1 −18, 2 5.0, 3 1500, 11 5.0), and the meter shows the core's reduction before Blend, as on the Kinky's Mix. Only Release reaches the core (pid 3); no knob writes Attack, so the meter's Attack is the LA Studio Comp's 10 ms fallback (the core's own is 5 ms).
- **Red Squeeze** [verified offline]: models 67 (mono, composite `0x6000ca`, shared with the LA Studio Comp) and 351 (stereo, `0x60001d`); knobs Sensitivity, Mix, Level. The node tree overrides slot 16 to `0x2710` (stereo 26 and 40), slot 18 to `0x225` and slot 6 to `0x2711`. Mono: the core runs on [1, 2] and writes over 1; Sensitivity is two `0x80` gains (one on the sidechain, one on the audio through `0x23f3` back into 1), and after the core come Level, `0x225`, four `0x102` filters and Mix (`0x89`, with the dry input). No knob reaches the core (core init: pid 1 −18, 2 5.0, 3 1500, 10 1500), so it had no writer, and so no path word, for `patch_comp.py` to reuse: it now writes the path `{1, slot}` into the parameter map's data, next to the writers' data. Stock blobs keep their path words just after the header (offsets 13..21). **[hypothesis]** the firmware takes a path word anywhere in the blob, as it does the writers' data. With no Attack or Release from the knobs, the meter uses the LA Studio Comp's 10 ms and 500 ms, while the core releases over 1500 ms.

## What the cores `0x2719` and `0x2710` compute [verified offline]

Both run process `0x8d342` (init `0x60d1db`, dm 17, pm 5); only their control functions differ. Read from the code and run in `ssim` with constant-amplitude 1 kHz squares, 2026-11-22. Simulation of the stock code; not yet measured on the unit.

- **Detector:** peak of the sidechain block (input 2) with a per-sample decay exp(−1/1920) (τ 40 ms, st15), multiplied by the current control gain (pm word 0), so the detector sees the output level: a **feedback** compressor. Then |·|, clip at st11, log2, × 6.0206 → level L in dB; over = L − st0.
- **Curve:** below −w/2 nothing; in the knee ΔG = k·(over + w/2)²/(2w); above it ΔG = k·over, with k = st2 = 1 − pid 8, w = pid 9 (st3 = w/2, st4 = 1/(2w)). With the feedback this gives ΔG = (1/pid8 − 1)·(x − T) above the knee, so **pid 8 is the ratio**. Gain = 2^(ΔG/6.0206), clamped to ≤ 1 and ≥ st1 (pid 4, the floor, i.e. maximum reduction).
- **Smoothing:** st14 = 0: one pole on the linear gain, st5 (pid 2) while the gain falls, st7 (pid 3) while it rises. st14 ≠ 0 (LA Studio only): a fast (st5/st7) and a slow (st6 = pid 12 / st8 = pid 11) pole both step from the current gain g, blended with w = min(g·st12, 1) on attack and w = min(g·st13, 1) on release (st12 = pid 13, st13 = 1/pid 14): new = w·slow + (1 − w)·fast. Heavy reduction releases on the fast pole, the last few dB (g near pid 14) on the slow one: a two-stage, program-dependent release. A second pole st9 (pid 7) smooths the gain for the audio path.
- **Output:** audio (input 1) × gain, clipped to ±st11 (pid 5), × st10 (pid 6). Every 300 blocks (st16) it calls `0x8cbb8` (**[hypothesis]** the meter report).
- **Time constants** (e.g. `0x60ccaa`): c = exp(−1/(96·v)) per sample for v in ms, so τ = 2·v ms.

| pid | `0x2719` (LA Studio) | state word | LA init |
|---|---|---|---|
| 1 | threshold dB | st0 | −32 (Type −38.5 / −35) |
| 2 | fast attack ms | st5 | 2.5 (τ 5 ms) |
| 3 | fast release ms | st7 | 400 (τ 0.8 s) |
| 4 | gain floor dB | st1 = 10^(v/20) | −40 |
| 5 | detector and output clip dB | st11 | 120 |
| 6 | output gain dB | st10 | 0 |
| 7 | audio-path gain smoothing ms | st9 | 1 |
| 8 | ratio | st2 = 1 − v | 2 (Type 3.35 / 100) |
| 9 | knee width dB | st3, st4 | 5 |
| 10 | two-stage on/off | st14 | 1 |
| 11 | slow release ms | st8 | 8000 (τ 16 s) |
| 12 | slow attack ms | st6 | 100000 (τ 200 s) |
| 13 | attack blend factor | st12 | 0.8 |
| 14 | release hand-over gain (linear) | st13 = 1/v | 0.7 (−3.1 dB) |

`0x2710`'s control function `0x60cf8e` takes pids 1..9 only (compare with 9 at `0x60cfa0`), same meanings, pid 4 floored at 0.001; the chains' pids 10..14 have no effect. The init leaves st14 = 0 (zeroed state), so all three `0x2710` models smooth in one stage.

Simulated static curves (gain reduction, dB; core input, without the blocks' gain stages):

| in dBFS | LA Compress | LA Limit | Red Squeeze (incl. −10 dB pid 6) | Kinky | Opto |
|---|---|---|---|---|---|
| −40 | −0.16 | 0.00 | −10.00 | 0.00 | −2.25 |
| −30 | −5.96 | −6.68 | −10.00 | 0.00 | −8.03 |
| −20 | −12.97 | −16.22 | −12.92 | −1.40 | −14.82 |
| −10 | −19.99 | −25.88 | −22.67 | −10.37 | −17.62 |
| 0 | −27.01 | −35.60 | −32.43 | −19.75 | −17.62 |

- LA Compress is 3.35:1 at −38.5 dB with a 5 dB knee (−20 dB in: −12.98 by the feedback formula, −12.97 simulated).
- LA Limit is nominally 100:1, but with the feedback the output settles inside the knee, just below the threshold (−35.9 … −35.6 dBFS): about 36:1 between −10 and 0 dB in. With the knee at 0.01 dB the output is −34.75 dB, exactly 100:1. Kinky (40:1, knee 5 dB) likewise gives about 16:1.
- Red Squeeze is 40:1 with a hard knee at −23 dB.
- Opto (9:1, threshold −27 dB, knee 40 dB) starts at −47 dBFS and stops at the floor, 17.6 dB of reduction (pid 4 −17.625).
- Red Squeeze and Kinky clip detector and core output at −7 dBFS (pid 5). In steady state the output sits near the threshold, so the clip only touches peaks that pass before the attack (**[hypothesis]**, from the code, not simulated).
- Model pids (blob member records): Red Squeeze 1 −23, 2 5, 3 1500, 4 −120, 5 −7, 6 −10, 8 40, 9 0; Kinky 1 −19.5, 4 −80, 5 −7, 8 40, 9 5 (2, 3 from the knobs); Opto 1 −27, 2 0.1, 4 −17.625, 5 0, 8 9, 9 40 (3 from Release). **[hypothesis]** these replace the init defaults (pid 1 −18, τ 10 ms / 2 s) that the sections above quote.

LA Studio step response (Compress, −60 → −10 dBFS and back): attack −0.9 dB at 2 ms, −6.9 at 10 ms, −17.7 at 50 ms, −19.8 at 100 ms (final −20.0); release holds for about 30 ms (peak detector), then −15.9 dB at 100 ms, −10.7 at 300 ms, −5.8 at 1 s, −3.1 at 4 s, −1.0 at 20 s.

## The Rochester Comp

- **Rochester Comp** [verified offline]: models 167 (mono, composite `0x600127`) and 466 (stereo, `0x600092`); knobs Gain, Threshold, Ratio, Attack, Release, Level, Knee, Mix. Its core is the Deluxe Comp's `0x271a`, straight from the composite (no node-tree override): mono slot 12 on [2, 3], writing over 2; stereo slots 29 and 22. Gain is two `0x80` stages, one on the audio after `0x2396`, one on the sidechain after its filters; Mix is `0x89` after the core. Threshold, Ratio, Attack, Release and Knee reach each core as pids 1, 2, 3, 4 and 8, as on the Deluxe Comp, so the meter reads the core's own gain and takes its coefficients from the core's state.

## Wiring and sharing

- **Where a core's output goes** [verified offline]: a member record's input and output buffer numbers (blob 0, `in_off`/`out_off`) show the core writes its output over one of its two inputs, and which one depends on the wiring, not the primitive: Deluxe Comp Mono `0x271a` in [0, 1] out [1]; Deluxe Stereo one core [4, 5] → [4], the other [0, 1] → [1]; LA Studio Mono `0x2719` [1, 2] → [1]; Kinky Mono `0x2710` [2, 3] → [2] (buffer 2 is the audio after Sensitivity's gain, 3 the sidechain); Red Squeeze `0x2710` [1, 2] → [1]; Opto `0x2710` [1, 2] → [1]; 3-Band and Rochester `0x271a` → input 0. So for the LA Studio and Kinky Comps the meter compared the audio with the untouched sidechain.
- **Shared cores** [verified offline]: besides the compressors, the Busy One amps and preamps (Ch1, Ch2, Jump) run `0x271a` and the Sweep Echo `0x2710`, and the 3-Band Comp has three `0x271a` cores per channel. The wrapper took every hooked core's peaks into one window max, so any of them in a preset could hide the metered compressor's reduction. The Deluxe Comp in a playing preset (Threshold −28.5, Ratio 6:1, Attack 38 ms, Release 300 ms, Knee 6) showed no lights at all at Mtr Thk 20 [verified on unit]; the preset's other blocks are not yet known. Since issue #84 every core but the metered one jumps straight to its stock code, since nine of them (two 3-Band Comps) in a preset near the DSP limit froze the unit (see [features/gr-meter.md](../../features/gr-meter.md)).

- **`0x456` is `0x271a`** [verified offline]: registry `0x200310` (`0x456`) has the same init (`0x6100f8`) and process (`0x8d9c9`) as `0x271a` (`0x200320`), and their control functions `0x610082` and `0x6100bd` are word for word the same (both call `0x60fe44` with 16 and 48000). So the legacy compressors on `0x456` (Blue Comp, Blue Comp Treb, Red Comp, Tube Comp, Vetta Comp, Vetta Juice, Boost Comp) are the Deluxe core with fixed pid values. There are only two compressor loops, `0x8d9c9` and `0x8d342`.

Node-tree overrides (why the Kinky Comp's core is `0x2710`): [model-blobs.md](../model-blobs.md#the-node-tree). The GR meter built on these cores: [features/gr-meter.md](../../features/gr-meter.md).

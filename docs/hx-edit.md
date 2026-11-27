# HX Edit with HXtra knobs

HX Edit knows each block's knobs from its own catalog. Knobs HXtra adds (Distortion Crossover's Frequency, Mix or Dry/Wet Level and Mode, the GR meter's position, direction and thickness, the FX Loop gate, Input Sidechain) aren't in that catalog, so HX Edit doesn't show them, and its exports (`.hlx`, `.hls`) drop them. Import such an export again and those knobs are back at their defaults.

`tools/hxedit_patch.py` adds the knobs your build has to a **copy** of HX Edit. The copy then shows them, exports them and imports them again. The installed HX Edit stays as it is.

**Status:** ran on an HX Stomp with HX Edit 3.82 on macOS. With v1.2.1-bass.2 (every feature ticked, run from RAM): the probe accepted every block, and HX Edit showed the new knobs with the values set on the unit. With a development build that has Distortion Crossover's Mix knob: the same, plus a preset exported, changed, imported and read back on the unit, exported again with the same values, and a setlist export (`.hls`) with the knobs.

## What you need

- A Mac. The patched copy is signed ad hoc with `codesign`, which only macOS has. Windows is not supported yet.
- HX Edit for firmware 3.80 (tested with HX Edit 3.82). After an HX Edit update, patch a new copy of it.
- Python 3.10 or newer with `msgpack`, and for the check on the unit `pyusb`: `pip install msgpack pyusb`.
- This repository (`git clone -b bass https://github.com/muetze-online/hxtra-bass.git`).
- Line 6's stock firmware `HX_Stomp_v3.80.0_0x103966115.hxf` (see [Building](building.md#requirements)).
- **The `.hxf` you installed** (from the setup page or `build.py`). The patch must fit that build exactly, so keep that file.

## Patch a copy

```sh
cd hxtra-bass
export HX_STOMP_HXF=path/to/HX_Stomp_v3.80.0_0x103966115.hxf

# what would change (writes nothing)
python3 tools/hxedit_patch.py plan path/to/HXtra_v1.2.1.hxf --app "/Applications/HX Edit.app"

# copy HX Edit and patch the copy
python3 tools/hxedit_patch.py patch "$HOME/Applications/HX Edit (HXtra knobs).app" path/to/HXtra_v1.2.1.hxf --from "/Applications/HX Edit.app"

# check the copy against the build (reads only)
python3 tools/hxedit_patch.py check "$HOME/Applications/HX Edit (HXtra knobs).app" path/to/HXtra_v1.2.1.hxf
```

`patch` refuses the installed HX Edit and patches a copy only. It removes the copy's quarantine flag and signs it ad hoc. A note in the copy (`Contents/Resources/hxtra-knobs.json`) records which build it was patched for: patching the same copy again changes nothing, and patching it for another build is refused. For a new build or a new HX Edit, make a new copy.

## Check it on the unit first

A patched HX Edit sends every block of a patched model with the patched number of knobs. Firmware that doesn't have those knobs drops the **whole preset** (an empty preset after an import). So before you use the copy, let `probe` check that the unit runs the build you patched for:

1. Quit HX Edit (any copy). The probe talks to the HX Stomp over USB.
2. On the HX Stomp, select a preset you can spare, best an empty one. The probe writes into its edit buffer only and saves nothing. To restore the preset afterwards, select it again.
3. Run:

```sh
python3 tools/hxedit_patch.py probe path/to/HXtra_v1.2.1.hxf
```

It writes one block for each kind of change and reads it back. "N of N accepted" means the copy fits. Any block dropped: don't use the copy, and check that the `.hxf` is the one installed.

## What it changes

Only files in `Contents/Resources` of the copy: `Helix.sym`, `HelixModelDefs.bin`, `HX_ModelCatalog.bin`, `HX_ModelCatalog.json` and `HelixControls.json`. Every value comes from your own files when you patch: the knobs are the ones your `.hxf` has beyond stock 3.80 (Entry 3 of both images), and each new knob gets the display type that stock knobs with the same range use (dB, Hz with Off, percent, on/off). List knobs whose labels no stock knob has, such as Mode (Full/Xover), get a display type of their own. With `--labels stock` they take a stock type with as many labels instead (Off/On for Full/Xover).

## Limits

- Snapshots 4–6, Tap command and preset colours live in HX Edit's program code, not in its catalog. The patch doesn't help with those: exports still hold snapshots 1–3 only.
- Models HX Edit doesn't know at all (NAM A2 Lite, the Metronome block) stay as they are.
- A stereo block shows the mono block's new knobs at their defaults, because HX Edit has one list for both. That's harmless: the unit gets the stereo block's own knobs only.
- HX Edit rounds percent knobs to whole percent (Blend 30 % can come back as 29 %) and writes that value when it saves.

## Don't share the patched app

It is Line 6's program with changed files. Patch your own copy and keep it to yourself, as with the firmware. This repository holds no Line 6 data: the tool reads everything from your files on your computer.

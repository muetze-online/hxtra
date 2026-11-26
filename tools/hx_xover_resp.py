#!/usr/bin/env python3
"""hx_xover_resp.py IMAGE OUTDIR [--models 302,386,431,595] [--freq 250] [--level -40] [--secs 6] [--wavdir DIR]
                   [--set KNOB=VALUE ...] [--sweep KNOB=V1,V2,... ...] [--tag SUFFIX] [--signal WAV]
: the Distortion Crossover (HX_DIST_XOVER) of HXtra bass measured on the HX Stomp over USB.

IMAGE is the .hxf running on the unit (its Entry 3 gives each model's knobs, its parameter map their defaults).
Per model (Table 1 preset IDs) the block goes alone into slot 0 of path A with its stock defaults and, for the
four crossover knobs, one case each (Freq fixed):

  wet      Full   Dry -60  Wet 0     the drive path alone (high-passed at Freq before the drive)
  full0    Full   Dry 0    Wet -60   the dry path alone: should be the input, flat at 0 dB
  full18   Full   Dry +18  Wet -60   as full0, 18 dB louder
  xover0   Xover  Dry 0    Wet -60   the dry path low-passed at Freq (LR4: -6 dB at Freq, -24 dB/oct above)
  off      Full   Dry -60  Wet -60   both down
  lvlmax   Full   Dry 0    Wet -60   Level at its maximum: the dry path moves by the Level curve's rise from its
                                     default (patch_dist.plan_model's 'level'; not in the default cases)

White noise from noise_mac (xorshift32 with a fixed seed, so this script rebuilds the very samples) goes into
USB 5/6, USB 1 is recorded. Each take is aligned to the rebuilt noise by cross-correlation; then
  lin = |S_yx / S_xx|  (the part of the output that is a linear filter of the input: the dry path and the linear
                        part of the drive path), in dB from USB 5/6 to USB 1 (the empty chain passes 0 dB),
  res = S_yy (1 - coherence) / S_xx  (what is not: distortion, noise; dB relative to the input in that band),
Welch, Hann, 8192 points, shown on 1/3-octave bands. hx_smoke.scan counts dropouts (zero runs), repeated blocks
and clicks a take.

Writes OUTDIR/response.csv (one row a band: Hz, then lin and res of each model/case), OUTDIR/summary.md (a table a model: lin and res at a few frequencies, dropouts) and the .wav takes to
--wavdir (default OUTDIR; 8 channels of 32-bit float, ~9 MB a take). The edit buffer is saved first and written
back at the end. One preset write a case, one for the input block, one for the restore: 22 for four models (stock
3.80 stops answering after about 30 a boot; this script refuses more than 28). --reanalyse reads the takes in
--wavdir again instead of measuring (the noise level and length must be the ones they were recorded with).

--signal WAV plays that recording (its first channel, 48 kHz) into USB 5/6 instead of the noise, once a take,
and only records: no spectra, no response.csv; summary.md lists each take's RMS, peak and dropouts, and the takes
are for a loudness tool (BS.1770) to compare. --level and --secs do not apply then.

Helpers (built once; HX_MAC_HELPERS names their folder, default build/):
  swiftc -O tools/mac/noise_mac.swift -o build/noise_mac
  swiftc -O tools/mac/record_mac.swift -o build/record_mac
"""
import argparse, json, os, struct, subprocess, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sharc"))
import hx_smoke as hs
import patch_dist as pdist
import pmap

ROOT = hs.ROOT
HELPERS = os.environ.get("HX_MAC_HELPERS", os.path.join(ROOT, "build"))
NOISE, RECORDER = os.path.join(HELPERS, "noise_mac"), os.path.join(HELPERS, "record_mac")
PLAYF32 = os.path.join(HELPERS, "playf32_mac")
FS, NFFT = 48000, 8192
CASES = (("wet", 0, -60.0, 0.0), ("full0", 0, 0.0, -60.0), ("full18", 0, 18.0, -60.0),
         ("xover0", 1, 0.0, -60.0), ("off", 0, -60.0, -60.0), ("lvlmax", 0, 0.0, -60.0))
DEFAULT_CASES = ("wet", "full0", "full18", "xover0", "off")
SHOW_HZ = (63, 125, 250, 500, 1000, 2000, 4000)
MAX_WRITES = 28


def noise(n, level_db):
    """noise_mac's first n samples: xorshift32 from 0x9e3779b9, Float(state) / Float(UInt32.max) * 2 - 1, times
    the amplitude, in float32 as Swift computes them."""
    s, out = 0x9E3779B9, np.empty(n, dtype=np.uint32)
    for k in range(n):
        s ^= (s << 13) & 0xFFFFFFFF
        s ^= s >> 17
        s ^= (s << 5) & 0xFFFFFFFF
        out[k] = s
    amp = np.float32(10.0 ** (level_db / 20.0) * 3.0 ** 0.5)
    v = out.astype(np.float32) / np.float32(4294967296.0) * np.float32(2) - np.float32(1)
    return (v * amp).astype(np.float64)


def ctl(*a, timeout=30):
    return hs.run("tools/hx_ctl.py", "--timeout", "8", *a, timeout=timeout)


def defaults(image, pids):
    """{pid: (model name, [default value in slot order], {knob name: index})} from IMAGE's Entry 3 and pmap."""
    l6ia, _ = hs.patch_amp.load_l6ia_from_any(image)
    lay = hs.entry3_layouts(l6ia)
    fw = pmap.Firmware(image)
    out = {}
    for pid in pids:
        name, ps = lay[pid]
        m = next(m for m in fw.e3["models"] if m["name"] == name)
        rep = {p["name"]: p for p in pmap.model_report(fw, m)["params"]}
        raw = {p["name"]: p["default"] for p in m["params"]}
        # a hidden knob (slot 10000: Smart of Bronze Master and Killer Z) is not in a preset; sent along, the unit
        # drops the preset (2026-11-22)
        ps = [p for p in ps if rep.get(p["name"], {}).get("slot") != pdist.pt.HIDDEN_SLOT]
        vals = []
        for p in ps:
            d = rep[p["name"]]["default"] if p["name"] in rep else p["lo"]
            if p["type"] == 2:      # Entry 3 holds an int knob's default as its bits (4 reads 5.6e-45; pmap rounds to 0)
                d = struct.unpack("<I", struct.pack("<f", raw.get(p["name"], d)))[0]
                d = min(max(d, int(p["lo"])), int(p["hi"]))
            # type 1 is a switch, a bool in a preset (Kinky Boost's Boost and Bright): sent as 0.0, the unit drops
            # the preset (2026-11-22)
            vals.append(int(d) if p["type"] == 2 else bool(d) if p["type"] == 1 else float(d))
        lname = pdist.LEVEL.get(name, pdist.LEVEL_KNOBS)
        lv = [i for i, p in enumerate(ps) if lname and p["name"] in ((lname,) if isinstance(lname, str) else lname)]
        out[pid] = (name, vals, {p["name"]: i for i, p in enumerate(ps)},
                    (lv[0], ps[lv[0]]["hi"]) if len(lv) == 1 else None)
    return out


def place(pid, vals, kind=1):
    a = ["set-model", "--slot", "0", "--model-id", str(pid), "--clear-after", "--clear-path-b"]
    if vals is not None:
        a += ["--kind", str(kind), "--params", ",".join(str(v) for v in vals)]
    r = ctl(*a)
    return "verified" in r.stdout, r.stdout + r.stderr


def usb_input(tmp):
    j = os.path.join(tmp, "edit_usb.json")
    ctl("get-preset", "-o", j)
    d = json.load(open(j)); d["preset"]["0"]["22"][0]["20"]["5"] = 6        # the input block: USB 5/6
    json.dump(d, open(j, "w"))
    return "verified" in ctl("load-preset", j).stdout


def take(wav, secs, level_db):
    play = subprocess.Popen([NOISE, "HX Stomp", str(secs + 2), "5,6", str(level_db)], stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    subprocess.run([RECORDER, "HX Stomp", str(secs), wav], capture_output=True, timeout=secs + 30)
    play.wait()
    x, rate = hs.read_wav(wav)
    assert rate == FS, f"{wav}: {rate} Hz"
    return np.asarray(x, dtype=np.float64)


def take_signal(wav, f32, n):
    """Record USB 1 while playf32_mac plays the float32 file f32 (n samples) once into USB 5/6."""
    secs = n / FS + 2.5
    rec = subprocess.Popen([RECORDER, "HX Stomp", f"{secs:.2f}", wav], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    subprocess.run([PLAYF32, "HX Stomp", f32, "5,6"], capture_output=True, timeout=secs + 30)
    rec.wait(timeout=secs + 30)


def align(y, u):
    """(lag, peak/median ratio): y[k] ~ u[k + lag], the lag where |xcorr| peaks."""
    n = 1 << int(np.ceil(np.log2(len(u) + len(y))))
    c = np.fft.irfft(np.fft.rfft(u, n) * np.conj(np.fft.rfft(y, n)), n)[:len(u) - len(y) + 1]
    a = np.abs(c)
    k = int(np.argmax(a))
    return k, float(a[k] / (np.median(a) + 1e-30))


def spectra(y, x):
    """Welch sums (S_xx, S_yy, S_yx) over Hann segments of NFFT, half overlap."""
    w = np.hanning(NFFT)
    sxx = syy = 0.0
    syx = 0j
    for k in range(0, len(y) - NFFT, NFFT // 2):
        X, Y = np.fft.rfft(x[k:k + NFFT] * w), np.fft.rfft(y[k:k + NFFT] * w)
        sxx = sxx + np.abs(X) ** 2
        syy = syy + np.abs(Y) ** 2
        syx = syx + Y * np.conj(X)
    return sxx, syy, syx


def bands(f, p, lo=20.0, hi=20000.0, per_oct=3):
    c = 1000.0 * 2 ** (np.arange(np.ceil(np.log2(lo / 1000) * per_oct), np.floor(np.log2(hi / 1000) * per_oct) + 1)
                        / per_oct)
    out = []
    for fc in c:
        m = (f >= fc * 2 ** (-0.5 / per_oct)) & (f < fc * 2 ** (0.5 / per_oct))
        out.append(p[m].mean() if m.any() else np.nan)
    return c, np.array(out)


def analyse(y, u):
    """(lag, xcorr peak ratio, band centres, |H|^2 a band, S_yy(1-coh) a band, S_xx a band); y trimmed."""
    y = y[int(0.3 * FS):]
    lag, q = align(y, u)
    sxx, syy, syx = spectra(y, u[lag:lag + len(y)])
    f = np.fft.rfftfreq(NFFT, 1 / FS)
    h2 = np.abs(syx) ** 2 / sxx ** 2
    res = np.maximum(syy - np.abs(syx) ** 2 / sxx, 0.0)
    c, h2b = bands(f, h2)
    _, resb = bands(f, res)
    _, sxxb = bands(f, sxx)
    return lag, q, c, h2b, resb, sxxb


def db(v):
    return 10 * np.log10(np.maximum(v, 1e-30))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("image")
    ap.add_argument("outdir")
    ap.add_argument("--models", default="302,386,431,595", help="Table 1 preset IDs (default Teemah!, Thrifter "
                    "Fuzz, Screamer, Pocket Fuzz)")
    ap.add_argument("--freq", type=float, default=250.0)
    ap.add_argument("--level", type=float, default=-40.0, help="noise RMS in dBFS (default -40)")
    ap.add_argument("--secs", type=float, default=6.0, help="seconds recorded a case (default 6)")
    ap.add_argument("--cases", default=",".join(DEFAULT_CASES))
    ap.add_argument("--wavdir")
    ap.add_argument("--reanalyse", action="store_true", help="evaluate the takes in --wavdir, no unit")
    ap.add_argument("--set", action="append", default=[], metavar="KNOB=VALUE",
                    help="an Entry 3 knob (e.g. ODLowBoost=1) set in every case of each model that has it")
    ap.add_argument("--sweep", action="append", default=[], metavar="KNOB=V1,V2,...",
                    help="one take per value (several --sweep: every combination) on top of each case; the take's "
                         "key gets _KNOBVALUE per swept knob (branch bass-mix: Mix and Gain)")
    ap.add_argument("--tag", default="", help="appended to each take's key (and wav name), so takes with other "
                    "--set values keep their own")
    ap.add_argument("--signal", help="a recording (WAV, 48 kHz; first channel) played instead of the noise; takes only")
    a = ap.parse_args()
    try:
        sets = tuple((k, float(v)) for k, v in (s.split("=", 1) for s in a.set))
        sweeps = [(k, [float(x) for x in v.split(",")]) for k, v in (s.split("=", 1) for s in a.sweep)]
    except ValueError:
        sys.exit("--set takes KNOB=VALUE and --sweep KNOB=V1,V2,..., the values numbers")
    import itertools
    combos = list(itertools.product(*[[(k, x) for x in vs] for k, vs in sweeps])) or [()]
    pids = [int(p) for p in a.models.split(",")]
    cases = [c for c in CASES if c[0] in a.cases.split(",")]
    writes = len(pids) * len(cases) * len(combos) + 2
    if writes > MAX_WRITES:
        sys.exit(f"{writes} preset writes: more than {MAX_WRITES} a boot; fewer models or cases")
    if not a.reanalyse:
        for tool in (PLAYF32 if a.signal else NOISE, RECORDER):
            if not os.path.exists(tool):
                sys.exit(f"missing {tool}: build it as the docstring says (HX_MAC_HELPERS names the folder)")
    wavdir = a.wavdir or a.outdir
    os.makedirs(a.outdir, exist_ok=True); os.makedirs(wavdir, exist_ok=True)
    lay = defaults(a.image, pids)
    if a.signal:
        sig, rate = hs.read_wav(a.signal)
        assert rate == FS, f"{a.signal}: {rate} Hz, not {FS}"
        f32 = os.path.join(a.outdir, "signal.f32")
        np.asarray(sig, dtype="<f4").tofile(f32)
    else:
        u = noise(int((a.secs + 3.5) * FS), a.level)
    runs = []
    for pid in pids:
        name, vals, idx, level = lay[pid]
        for tag, mode, dry, wet in cases:
            v = list(vals)
            # a model with fewer of the four knobs (Obsidian 7000: Freq and Mode; a SKIP model: none) gets those it has
            for knob, val in (("Frequency", a.freq), ("Dry Level", dry), ("Wet Level", wet), ("Mode", mode)):
                if knob in idx:
                    v[idx[knob]] = val
            for knob, val in sets:              # in the knob's own type: a switch as a bool, a list as an int
                if knob in idx:
                    v[idx[knob]] = type(v[idx[knob]])(val)
            if tag == "lvlmax":
                if level is None:
                    continue
                v[level[0]] = level[1]
            for combo in combos:
                w = list(v)
                for knob, val in combo:
                    if knob not in idx:
                        sys.exit(f"{name} has no knob {knob!r} to sweep")
                    w[idx[knob]] = type(w[idx[knob]])(val)
                runs.append((f"{pid}_{tag}" + "".join(f"_{k}{x:g}" for k, x in combo) + a.tag, pid, w))
    res = {}

    def evaluate(key, wav):
        if a.signal:
            rms, peak, rep, drop, clicks = hs.scan(wav)
            res[key] = dict(rms=rms, peak=peak, rep=rep, drop=drop, clicks=clicks)
            print(f"{key:12} rms {rms:6.1f} dBFS  peak {peak:6.1f}  drop {drop} rep {rep} click {clicks}", flush=True)
            return
        y = np.asarray(hs.read_wav(wav)[0], dtype=np.float64)
        rms, peak, rep, drop, clicks = hs.scan(wav)
        lag, q, c, h2, rs, sxx = analyse(y, u)
        res[key] = dict(rms=rms, peak=peak, rep=rep, drop=drop, clicks=clicks, lag=lag, q=q, c=c, h2=h2, rs=rs, sxx=sxx)
        print(f"{key:12} rms {rms:6.1f} dBFS  drop {drop} rep {rep} click {clicks}  lag {lag} xcorr {q:.0f}",
              flush=True)

    if a.reanalyse:
        for key, _, _ in runs:
            wav = os.path.join(wavdir, key + ".wav")
            if os.path.exists(wav):
                evaluate(key, wav)
    else:
        if not hs.alive(2):
            sys.exit("the HX Stomp does not answer (USB connected? HX Edit closed?)")
        backup = os.path.join(a.outdir, "edit_buffer_before.json")
        ctl("get-preset", "-o", backup)
        usb_set = False
        try:
            for key, pid, vals in runs:
                ok, log = place(pid, vals)
                if ok and not usb_set:
                    ok = usb_set = usb_input(a.outdir)
                if not ok:
                    print(f"{key:12} not placed:\n{log}", flush=True)
                    continue
                time.sleep(0.5)
                wav = os.path.join(wavdir, key + ".wav")
                if a.signal:
                    take_signal(wav, f32, len(sig))
                else:
                    take(wav, a.secs, a.level)
                evaluate(key, wav)
        finally:
            r = ctl("load-preset", backup)
            print("edit buffer restored" if "verified" in r.stdout else f"edit buffer NOT restored:\n{r.stdout}{r.stderr}")
    if not res:
        sys.exit("nothing recorded")
    if a.signal:
        lines = [f"# Distortion Crossover, Aufnahme durchgespielt ({time.strftime('%Y-%m-%d %H:%M')})", "",
                 f"Image `{os.path.basename(a.image)}`, Freq {a.freq:g} Hz, Signal `{os.path.basename(a.signal)}`.", "",
                 "| Aufnahme | RMS dBFS | Spitze dBFS | Aussetzer/Wdh./Klicks |", "|---|---|---|---|"]
        for key, _, _ in runs:
            r = res.get(key)
            lines.append(f"| {key} | nicht gesetzt |" if r is None else
                         f"| {key} | {r['rms']:.1f} | {r['peak']:.1f} | {r['drop']}/{r['rep']}/{r['clicks']} |")
        open(os.path.join(a.outdir, "summary.md"), "w").write("\n".join(lines) + "\n")
        print("\n".join(lines))
        return
    cols = {}
    for key, r in res.items():
        c = r["c"]
        cols[key + "_lin"] = db(r["h2"])
        cols[key + "_res"] = db(r["rs"] / r["sxx"])
    with open(os.path.join(a.outdir, "response.csv"), "w") as fh:
        fh.write("Hz," + ",".join(cols) + "\n")
        for k, fc in enumerate(c):
            fh.write(f"{fc:.1f}," + ",".join(f"{cols[n][k]:.2f}" for n in cols) + "\n")
    sel = [int(np.argmin(np.abs(c - hz))) for hz in SHOW_HZ]
    lines = [f"# Distortion Crossover, gemessen ({time.strftime('%Y-%m-%d %H:%M')})", "",
             f"Image `{os.path.basename(a.image)}`, Freq {a.freq:g} Hz, Rauschen {a.level:g} dBFS, {a.secs:g} s je Fall. "
             "lin = linearer Anteil |H| in dB von USB 5/6 nach USB 1; res = nichtlinearer Rest + Rauschen in dB "
             "zum Eingang im Band; xcorr = Spitze/Median der Kreuzkorrelation (klein: kaum lineare Kopie des "
             "Eingangs im Ausgang).", ""]
    for pid in pids:
        lines += ["", f"## {lay[pid][0]} ({pid})", "",
                  "| Fall | " + " | ".join(f"{hz} Hz" for hz in SHOW_HZ) + " | RMS | Aussetzer/Wdh./Klicks | xcorr |",
                  "|---|" + "---|" * (len(SHOW_HZ) + 3)]
        for key in (k for k, q, _ in runs if q == pid):
            tag = key[len(f"{pid}_"):]
            if key not in res:
                lines.append(f"| {tag} | nicht gesetzt |")
                continue
            r = res[key]
            cell = [f"{cols[key + '_lin'][k]:+.1f} / {cols[key + '_res'][k]:+.0f}" for k in sel]
            lines.append(f"| {tag} | " + " | ".join(cell) + f" | {r['rms']:.1f} | {r['drop']}/{r['rep']}/{r['clicks']} | {r['q']:.0f} |")
    lines += ["", "Zellen: lin / res (dB)."]
    open(os.path.join(a.outdir, "summary.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

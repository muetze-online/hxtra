#!/usr/bin/env python3
"""hx_freqresp.py OUTDIR [--level DBFS] [--secs S] : magnitude response of a stock block on the HX Stomp
over USB, for several knob settings.

Each case puts one block in slot 0 of path A (everything else cleared), white noise from build/noise_mac
goes into USB 5/6 (the input block set to USB 5/6 once, after the first case) and USB 1 is recorded with
build/record_mac. |H(f)|^2 = PSD(case) / PSD(dry), the dry case being a Volume Pedal block (260) alone; Welch
PSDs, Hann window, 8192 points (5.9 Hz bins at 48 kHz), shown on 1/6-octave bands.
A low --level (default -40 dBFS RMS) keeps a drive block's clipper out of the way: the case 'lin' repeats one
setting 10 dB louder, so the two curves agree when the block is linear there.

Writes OUTDIR/<case>.wav, OUTDIR/response.csv (band centre Hz, one dB column a case) and OUTDIR/summary.txt
(each case's -3 dB points relative to its own maximum). The edit buffer is saved first and written back at the
end. Needs the installed firmware to answer hx_ctl (stock 3.80.0 or an HXtra build), HX Edit closed.
Each set-model is one preset write: stock 3.80 stops answering after about 30 a boot; this run makes 18
(16 cases, the input block, the restore).

Build the helpers once:
  swiftc -O tools/mac/noise_mac.swift -o build/noise_mac
  swiftc -O tools/mac/record_mac.swift -o build/record_mac
"""
import argparse, json, os, subprocess, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hx_smoke as hs

ROOT = hs.ROOT
NOISE = os.path.join(ROOT, "build", "noise_mac")
TEEMAH, DRY = 302, 260                   # Table 1 preset IDs: Teemah! (HD2_DistTeemahMono), Volume Pedal
# Teemah! knobs in Entry 3 slot order: Gain, Bass (Cut), Treble (Cut), Clipping (int list), Level
def teemah(bass, treble, gain=0.0, clip=0, level=0.63):
    return f"{gain:.3f},{bass:.3f},{treble:.3f},{clip},{level:.3f}"

CASES = [("dry", DRY, None, 0)]
CASES += [(f"bass{int(v * 100):03d}", TEEMAH, teemah(v, 0.5), 0) for v in (0.0, 0.25, 0.5, 0.75, 1.0)]
CASES += [(f"treble{int(v * 100):03d}", TEEMAH, teemah(0.5, v), 0) for v in (0.0, 0.25, 0.75, 1.0)]
CASES += [("clip1", TEEMAH, teemah(0.5, 0.5, clip=1), 0), ("clip2", TEEMAH, teemah(0.5, 0.5, clip=2), 0),
          ("gain050", TEEMAH, teemah(0.5, 0.5, gain=0.5), 0), ("lin", TEEMAH, teemah(0.5, 0.5), 10),
          ("dry_end", DRY, None, 0)]


def ctl(*a, timeout=20):
    return hs.run("tools/hx_ctl.py", "--timeout", "8", *a, timeout=timeout)


def place(pid, params):
    a = ["set-model", "--slot", "0", "--model-id", str(pid), "--clear-after", "--clear-path-b"]
    if params:
        a += ["--kind", "1", "--params", params]
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
    subprocess.run([hs.RECORDER, "HX Stomp", str(secs), wav], capture_output=True, timeout=secs + 30)
    play.wait()
    x, rate = hs.read_wav(wav)
    return np.asarray(x[int(0.3 * rate):], dtype=np.float64), rate


def psd(x, n=8192):
    w = np.hanning(n); hop = n // 2
    segs = [x[k:k + n] * w for k in range(0, len(x) - n, hop)]
    return np.mean([np.abs(np.fft.rfft(s)) ** 2 for s in segs], axis=0)


def bands(f, p, lo=20.0, hi=20000.0, per_oct=6):
    c = lo * 2 ** (np.arange(int(np.log2(hi / lo) * per_oct) + 1) / per_oct)
    out = []
    for fc in c:
        m = (f >= fc * 2 ** (-0.5 / per_oct)) & (f < fc * 2 ** (0.5 / per_oct))
        out.append(p[m].mean() if m.any() else np.nan)
    return c, np.array(out)


def corners(c, db):
    """Band centres where the curve crosses its maximum - 3 dB (log-interpolated)."""
    t = np.nanmax(db) - 3.0
    out = []
    for k in range(1, len(c)):
        a, b = db[k - 1] - t, db[k] - t
        if np.isfinite(a) and np.isfinite(b) and (a < 0) != (b < 0):
            r = a / (a - b)
            out.append(round(float(np.exp(np.log(c[k - 1]) + r * np.log(c[k] / c[k - 1]))), 1))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outdir")
    ap.add_argument("--level", type=float, default=-40.0, help="noise RMS in dBFS (default -40)")
    ap.add_argument("--secs", type=float, default=8.0, help="seconds recorded a case (default 8)")
    a = ap.parse_args()
    for tool in (NOISE, hs.RECORDER):
        if not os.path.exists(tool):
            sys.exit(f"missing {tool}: build it as the docstring says")
    os.makedirs(a.outdir, exist_ok=True)
    if not hs.alive(2):
        sys.exit("the HX Stomp does not answer (USB connected? HX Edit closed?)")
    backup = os.path.join(a.outdir, "edit_buffer_before.json")
    ctl("get-preset", "-o", backup)
    print(f"edit buffer saved to {backup}", flush=True)
    spectra, rate, usb_set = {}, 48000, False
    try:
        for name, pid, params, louder in CASES:
            ok, log = place(pid, params)
            if ok and not usb_set:
                ok = usb_set = usb_input(a.outdir)
            if not ok:
                print(f"{name:10} not placed:\n{log}", flush=True)
                continue
            wav = os.path.join(a.outdir, name + ".wav")
            x, rate = take(wav, a.secs, a.level + louder)
            rms = 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)
            spectra[name] = (psd(x), louder)
            print(f"{name:10} recorded {len(x) / rate:.1f} s, {rms:.1f} dBFS", flush=True)
    finally:
        r = ctl("load-preset", backup)
        print("edit buffer restored" if "verified" in r.stdout else f"edit buffer NOT restored:\n{r.stdout}{r.stderr}")
    if "dry" not in spectra:
        sys.exit("no dry reference recorded")
    f = np.fft.rfftfreq(8192, 1 / rate)
    ref = spectra["dry"][0]
    cols, lines = {}, []
    for name, (p, louder) in spectra.items():
        c, h = bands(f, p / ref)
        cols[name] = 10 * np.log10(h) - louder
        lines.append(f"{name:10} -3 dB points (Hz): {corners(c, cols[name])}   max {np.nanmax(cols[name]):.1f} dB "
                     f"at {c[int(np.nanargmax(cols[name]))]:.0f} Hz")
    with open(os.path.join(a.outdir, "response.csv"), "w") as fh:
        fh.write("Hz," + ",".join(cols) + "\n")
        for k, fc in enumerate(c):
            fh.write(f"{fc:.1f}," + ",".join(f"{cols[n][k]:.2f}" for n in cols) + "\n")
    open(os.path.join(a.outdir, "summary.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

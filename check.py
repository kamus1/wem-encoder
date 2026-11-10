"""Checks build/encode + wem.py against real Wwise output.

For each folder with song.wav + song.wem made by Wwise from that .wav:
  1. song.wav -> Ogg (aoTuV) -> our .wem.
  2. Our .wem decoded with ww2ogg (an independent implementation) must equal our Ogg decoded with ffmpeg,
     sample for sample: the repackaging is lossless.
  3. Start offset, length and SNR against song.wav, next to the same numbers for Wwise's .wem.
  4. Header fields next to Wwise's.

Usage: python check.py <folder> [more folders] [--name song_preview] [--quality 4]
       [--encoder "wine build/encode.exe"]   (any encoder command; default build/encode)
       [--tools DIR]   (folder with ww2ogg and revorb; default: found in PATH)
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wem  # noqa: E402

ROOT = wem.ROOT
TOOLS: Path | None = None            # folder with ww2ogg and revorb; None: look them up in PATH
ENCODER = [str(ROOT / "build" / "encode")]


def tool(name: str) -> str:
    if TOOLS is not None:
        return str(TOOLS / name)
    found = shutil.which(name)
    if not found:
        sys.exit(f"check: {name} not found in PATH (pass --tools DIR)")
    return found


def run(*args) -> None:
    subprocess.run([str(a) for a in args], check=True, capture_output=True)


def decode_wem(path: Path, tmp: Path) -> np.ndarray:
    ogg = tmp / (path.stem + "_ww2ogg.ogg")
    run(tool("ww2ogg"), path, "-o", ogg, "--pcb", wem.CODEBOOKS)
    run(tool("revorb"), ogg)
    return decode_ogg(ogg, tmp)


def decode_ogg(path: Path, tmp: Path) -> np.ndarray:
    out = tmp / (path.stem + "_dec.wav")
    run("ffmpeg", "-v", "error", "-y", "-i", path, "-c:a", "pcm_s16le", out)
    return sf.read(out, dtype="int16")[0]


def start_lag(dec: np.ndarray, ref: np.ndarray, rate: int) -> int:
    """Lag (samples) of dec against ref around the first sound, by cross-correlation (±2000 samples)."""
    r = ref.astype(np.float64).mean(1)
    d = dec.astype(np.float64).mean(1)
    start = int(np.argmax(np.abs(r) > 300))
    seg = r[start:start + rate * 3]
    best, lag = -np.inf, 0
    for k in range(-2000, 2001):
        a = start + k
        if a < 0 or a + len(seg) > len(d):
            continue
        c = float(np.dot(d[a:a + len(seg)], seg))
        if c > best:
            best, lag = c, k
    return lag


def snr(dec: np.ndarray, ref: np.ndarray) -> float:
    n = min(len(dec), len(ref))
    r = ref[:n].astype(np.float64)
    e = dec[:n].astype(np.float64) - r
    return 10 * np.log10(np.sum(r ** 2) / max(np.sum(e ** 2), 1e-9))


def check(folder: Path, name: str, quality: str, encoder: list[str] = ENCODER) -> bool:
    wav, ref_wem = folder / f"{name}.wav", folder / f"{name}.wem"
    ref_audio, rate = sf.read(wav, dtype="int16")
    ok = True
    print(f"== {folder.name}/{name} ({len(ref_audio) / rate:.1f} s)")
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        ogg, ours = tmp / "ours.ogg", tmp / "ours.wem"
        run(*encoder, wav, ogg, quality)
        ours.write_bytes(wem.ogg_to_wem(ogg.read_bytes()))
        from_ogg = decode_ogg(ogg, tmp)
        from_wem = decode_wem(ours, tmp)
        info = wem.read_wem(ours.read_bytes())
        extra = info["vorb"]["last_granule_extra"]
        n = len(from_ogg)
        same = len(from_wem) >= n and np.array_equal(from_wem[:n], from_ogg)
        ok &= same
        print(f"  lossless repackaging (ww2ogg(our wem) == our ogg): {'YES' if same else 'NO'}"
              f"  [{len(from_wem)} vs {n} samples, wem says +{extra} of padding]")
        ok &= len(from_wem) - extra == len(ref_audio)
        rows = [("ours", ours.stat().st_size, from_wem, info)]
        if ref_wem.exists():
            rows.append(("wwise", ref_wem.stat().st_size, decode_wem(ref_wem, tmp), wem.read_wem(ref_wem.read_bytes())))
        for label, size, dec, w in rows:
            lag = start_lag(dec, ref_audio, rate)
            length = len(dec) - w["vorb"]["last_granule_extra"]
            print(f"  {label:5s}: {size:>9,} bytes  start lag {lag:+d} samples  length {length} "
                  f"({'=' if length == len(ref_audio) else '!='} wav)  SNR {snr(dec, ref_audio):.1f} dB")
            if label == "ours":
                ok &= lag == 0
        if len(rows) == 2:
            a, b = rows[0][3], rows[1][3]
            fields = [f"{k}: {a['vorb'][k]} vs {b['vorb'][k]}" for k in a["vorb"] if a["vorb"][k] != b["vorb"][k]]
            top = [f"{k}: {a[k]} vs {b[k]}" for k in ("tag", "channels", "rate", "cb_size", "ext", "mask") if a[k] != b[k]]
            print(f"  fields that differ (ours vs wwise): {'; '.join(top + fields) or 'none'}")
            print(f"  chunks: ours {a['chunks']}, wwise {b['chunks']}")
    print(f"  -> {'OK' if ok else 'FAIL'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="+", type=Path)
    ap.add_argument("--name", default="song", help="file name without extension (song, song_preview)")
    ap.add_argument("--quality", default="4")
    ap.add_argument("--encoder", help="encoder command, split on spaces (default build/encode)")
    ap.add_argument("--tools", type=Path, help="folder with ww2ogg and revorb (default: found in PATH)")
    a = ap.parse_args()
    global TOOLS
    TOOLS = a.tools
    encoder = a.encoder.split() if a.encoder else ENCODER
    results = [check(f, a.name, a.quality, encoder) for f in a.folders]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

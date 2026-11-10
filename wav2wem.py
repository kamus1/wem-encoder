"""PCM WAV -> Wwise Vorbis .wem without Wwise: build/encode (aoTuV) + wem.py.

Usage: python wav2wem.py in.wav out.wem [--quality 4]
Builds build/encode first if it is missing (build.sh). On Windows it uses build/encode.exe: download it from
the releases, or cross-compile it on Linux with `sh build.sh --windows`.
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wem  # noqa: E402

ENCODER = wem.ROOT / "build" / ("encode.exe" if os.name == "nt" else "encode")
QUALITY = "4"           # like Wwise's "Vorbis Quality High (Custom)" preset in the Rocksmith template


def ensure_encoder() -> Path:
    if not ENCODER.exists():
        if os.name == "nt":
            sys.exit(f"wav2wem: {ENCODER} is missing. Download encode.exe from the releases, or cross-compile it "
                     "on Linux with `sh build.sh --windows` (needs mingw-w64-gcc), and put it at that path.")
        subprocess.run(["sh", str(wem.ROOT / "build.sh")], check=True, capture_output=True)
    return ENCODER


def wav_to_wem(wav: Path, out: Path, quality: str = QUALITY) -> None:
    with tempfile.TemporaryDirectory() as t:
        ogg = Path(t) / "audio.ogg"
        subprocess.run([str(ensure_encoder()), str(wav), str(ogg), quality], check=True)
        out.write_bytes(wem.ogg_to_wem(ogg.read_bytes()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav", type=Path)
    ap.add_argument("wem", type=Path)
    ap.add_argument("--quality", default=QUALITY)
    a = ap.parse_args()
    wav_to_wem(a.wav, a.wem, a.quality)
    return 0


if __name__ == "__main__":
    sys.exit(main())

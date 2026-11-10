# wem-encoder

Turns a PCM `.wav` into a Wwise Vorbis `.wem` without Wwise. It works on Linux and Windows, and it is the inverse of [ww2ogg](https://github.com/hcs64/ww2ogg).

It was made for Rocksmith 2014 CDLC, the only place it has been tested. Other games that use Wwise 2019–2023 Vorbis may work, but nobody has checked.

```
song.wav ─ build/encode (aoTuV b6.03, VBR q4) ─▶ Ogg Vorbis ─ wem.py (repackaging) ─▶ song.wem
```

## Use

```sh
sh build.sh                                   # downloads aoTuV and builds build/encode (once; needs a C compiler, make, libogg)
python wav2wem.py song.wav song.wem           # builds the encoder first if it is missing
sh build.sh --windows                         # cross-compiles build/encode.exe for Windows (needs mingw-w64-gcc)
```

- **Input:** a 16-bit PCM WAV, any rate, 1–8 channels.
- **On Windows,** `wav2wem.py` uses `build\encode.exe`. Download it from the [releases](https://github.com/kamus1/wem-encoder/releases) or cross-compile it on Linux with `sh build.sh --windows`. GitHub Actions builds each release from the tagged source, with a sha256 to check the download. It is static and needs only DLLs that ship with Windows.
- **Checking against Wwise:** `check.py` compares the result with a `.wem` that Wwise made from the same `.wav`. It needs [ww2ogg](https://github.com/hcs64/ww2ogg) and revorb, found in the PATH or in a folder given with `--tools`.

```sh
python check.py <folder with song.wav + song.wem> [--name song_preview] [--tools DIR]
python check.py <folder> --encoder "wine build/encode.exe"   # same check for the Windows build
```

## Status

**Works in Rocksmith 2014.** Songs built with it play in the game (Steam on Linux through Proton 10, CDLC Enabler):
- the preview plays;
- the full song plays to the end (tested up to 8.5 min);
- notes stay in sync;
- riff repeater and section restart seek to the right place.

An A/B test packaged the same songs twice, once with this encoder's `.wem` and once with Wwise's. The timing felt the same.

`check.py` on 4 songs (4–9 min) and 2 previews, against Wwise 2023.1's `.wem` from the same `.wav`:
- **Lossless repackaging:** ww2ogg decodes our `.wem` sample-identical to our Ogg.
- **Timing:** start offset 0 samples and exact length, like Wwise.
- **Quality:** SNR against the `.wav` is 18–22 dB for ours and 12.6–16 dB for Wwise. Our files are 1–9 % smaller.

The Windows build passes the same check, both under Wine and on a real Windows 11. Its output differs from the Linux build by a few bytes, because MinGW's math library rounds differently. Paths with non-ASCII characters work.

## Format

This is what Wwise 2019–2023 writes, as found in Rocksmith CDLC.

- **RIFF/WAVE.** No pad byte after an odd-sized chunk: Wwise does not write it, and ww2ogg rejects it.
  - `fmt ` (0x42 bytes): tag `0xFFFF`, channels, rate, avg bytes/s, block align 0, bits 0, cbSize `0x30`, 2 bytes 0, channel mask (`0x3` stereo), then the 0x2A-byte `vorb` block.
  - `data`.
  - Wwise 2023 also writes a 16-byte `hash` chunk. Files from older Wwise versions do not have it and play, so it is left out.
- **`vorb`** (little endian):

  | Offset | Field | Value |
  |---|---|---|
  | 0x00 | samples | exact sample count (the Ogg's last granule) |
  | 0x04 | loop start | offset of the first audio packet = setup packet size + 2 |
  | 0x08 | loop end | end of the audio = data size − seek table size |
  | 0x0C, 0x0E | loop begin/end extra | 0, last granule extra |
  | 0x10 | seek table size | |
  | 0x14 | data offset | seek table size + setup packet size + 2 |
  | 0x18 | max packet | largest audio packet (without its 2-byte size) |
  | 0x1A | last granule extra | decoded samples (sum over packets) − sample count |
  | 0x1C, 0x20 | decode alloc (x86, x64) | 16080, 16560 in every file checked (48 and 44.1 kHz stereo) |
  | 0x24 | codebook hash | **32-bit FNV-1 of the stripped setup packet** (checked at 48 and 44.1 kHz) |
  | 0x28, 0x29 | block sizes | log2 short/long (8, 11) |

- **avg bytes/s** = (data size − seek table size) × rate ÷ samples, rounded down (matches 7 files).
- **`data`** = seek table, then the setup packet, then the audio packets. Each packet is prefixed by its size (u16).
  - The identification and comment headers are not stored.
- **Seek table** (byte-identical to Wwise on 5 files):
  - Entries are (u16 frames, u16 bytes).
  - Walk the audio packets adding up their samples. When the sum since the last entry reaches 16384, counting the current packet, add an entry: (that sum, start offset of the current packet − start offset at the previous entry). 16384 is granularity 6, the setting in Rocksmith's Wwise template.
  - Offsets are counted from the start of the setup packet. There is no entry for the remainder.
- **Setup packet, stripped:**
  - 8 bits for the codebook count − 1, then one **10-bit index** per codebook into `packed_codebooks_aoTuV_603.bin`. Wwise uses the aoTuV static codebooks, so an aoTuV Ogg's codebooks are all in it.
  - No time domain section.
  - Floors without their 16-bit type.
  - Residue type in 2 bits instead of 16.
  - Mappings without their type.
  - Modes without their window and transform types.
  - No framing bit.
- **Audio packets:** the packet-type bit is removed and, in long blocks, the previous and next window flags too. The decoder derives them from the neighbouring packets' modes.

Wwise's own encoder picks 2 of the 42 codebooks differently from aoTuV b6.03-2020 (ids 417/420 against our 387/405). They have the same dimensions and entries, so the decoder needs the same memory. This is why our setup, and its hash, differ from Wwise's.

## License

MIT (see [LICENSE](LICENSE)). The format was worked out from real files and the Vorbis spec. Third-party parts are listed in [THIRD_PARTY.md](THIRD_PARTY.md):
- aoTuV and libogg, downloaded by `build.sh` and statically linked into the binaries;
- the codebook library file from ww2ogg.

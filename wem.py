"""Ogg Vorbis -> Wwise Vorbis .wem, without Wwise (the inverse of ww2ogg).

A .wem is the same Vorbis audio with a different wrapping:
  - RIFF/WAVE container, codec 0xFFFF, with a "vorb" block (sample count, offsets, block sizes) inside fmt.
  - data chunk = seek table + setup packet + audio packets, each packet prefixed by its 2-byte size.
  - The identification and comment headers are dropped (their info is in fmt/vorb).
  - Setup packet "stripped": each codebook is replaced by its 10-bit index in Wwise's codebook library
    (the aoTuV b6.03 static codebooks, packed_codebooks_aoTuV_603.bin), and the fields that are always the same
    (time domain, floor/mapping types, window/transform types, framing bit) are removed.
  - Audio packets "modified": the packet-type bit and the previous/next window flags are removed (the decoder
    infers them from the neighbouring packets' modes).

The Ogg stream must come from aoTuV b6.03 (build/encode), so that every codebook is in the library.

Usage:
  python wem.py in.ogg out.wem                 # convert
  python wem.py --info file.wem [...]          # dump a .wem's header fields
"""

import struct
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CODEBOOKS = ROOT / "packed_codebooks_aoTuV_603.bin"     # from ww2ogg (BSD), see THIRD_PARTY.md

# Constant fields of the Wwise 2019–2023 output for stereo aoTuV setups (same in every Rocksmith CDLC checked)
DECODE_ALLOC = 16080
DECODE_X64_ALLOC = 16560
SEEK_GRANULARITY = 16384        # frames per seek-table entry ("SeekTableGranularity" 6 in the Rocksmith template)


class WemError(Exception):
    pass


# ── Bits (Vorbis packs bits LSB first) ────────────────────────────────────────────────────────────────

class BitReader:
    def __init__(self, data: bytes):
        self.v = int.from_bytes(data, "little")
        self.pos = 0
        self.n = len(data) * 8

    def read(self, bits: int) -> int:
        if self.pos + bits > self.n:
            raise WemError("read past the end of a packet")
        x = (self.v >> self.pos) & ((1 << bits) - 1)
        self.pos += bits
        return x


class BitWriter:
    def __init__(self):
        self.v = 0
        self.pos = 0

    def write(self, value: int, bits: int) -> None:
        self.v |= (value & ((1 << bits) - 1)) << self.pos
        self.pos += bits

    def copy(self, r: BitReader, bits: int) -> int:
        x = r.read(bits)
        self.write(x, bits)
        return x

    def bytes(self) -> bytes:
        return self.v.to_bytes((self.pos + 7) // 8, "little")


def ilog(v: int) -> int:
    return v.bit_length() if v > 0 else 0


def lookup1_values(entries: int, dims: int) -> int:
    """Largest r with r**dims <= entries (Vorbis spec 9.2.3)."""
    r = int(round(entries ** (1.0 / dims)))
    while r ** dims > entries:
        r -= 1
    while (r + 1) ** dims <= entries:
        r += 1
    return r


# ── Codebooks ─────────────────────────────────────────────────────────────────────────────────────────

def read_codebook(r: BitReader, w: BitWriter) -> None:
    """Copies one standard Vorbis codebook from r to w (spec 3.2.1)."""
    if w.copy(r, 24) != 0x564342:
        raise WemError("bad codebook sync pattern")
    dims = w.copy(r, 16)
    entries = w.copy(r, 24)
    if w.copy(r, 1):                                # ordered
        w.copy(r, 5)
        cur = 0
        while cur < entries:
            cur += w.copy(r, ilog(entries - cur))
    else:
        sparse = w.copy(r, 1)
        for _ in range(entries):
            if not sparse or w.copy(r, 1):
                w.copy(r, 5)
    lookup = w.copy(r, 4)
    if lookup in (1, 2):
        w.copy(r, 32)
        w.copy(r, 32)
        value_bits = w.copy(r, 4) + 1
        w.copy(r, 1)
        n = lookup1_values(entries, dims) if lookup == 1 else entries * dims
        for _ in range(n):
            w.copy(r, value_bits)
    elif lookup != 0:
        raise WemError(f"bad codebook lookup type {lookup}")


def rebuild_packed(data: bytes) -> bytes:
    """Wwise packed codebook -> standard Vorbis codebook bits (what ww2ogg does)."""
    r = BitReader(data)
    w = BitWriter()
    dims = r.read(4)
    entries = r.read(14)
    w.write(0x564342, 24)
    w.write(dims, 16)
    w.write(entries, 24)
    if w.copy(r, 1):                                # ordered
        w.copy(r, 5)
        cur = 0
        while cur < entries:
            cur += w.copy(r, ilog(entries - cur))
    else:
        length_bits = r.read(3)
        sparse = w.copy(r, 1)
        if length_bits == 0 or length_bits > 5:
            raise WemError("bad packed codebook")
        for _ in range(entries):
            if not sparse or w.copy(r, 1):
                w.write(r.read(length_bits), 5)
    lookup = r.read(1)
    w.write(lookup, 4)
    if lookup == 1:
        w.copy(r, 32)
        w.copy(r, 32)
        value_bits = w.copy(r, 4) + 1
        w.copy(r, 1)
        for _ in range(lookup1_values(entries, dims)):
            w.copy(r, value_bits)
    return w.bytes()


_library: dict[tuple[int, int], int] | None = None


def codebook_library() -> dict[tuple[int, int], int]:
    """{(bit length, bits as int): index} for the codebooks in Wwise's library."""
    global _library
    if _library is None:
        raw = CODEBOOKS.read_bytes()
        table = struct.unpack_from("<I", raw, len(raw) - 4)[0]
        count = (len(raw) - table) // 4
        offsets = struct.unpack_from(f"<{count}I", raw, table)
        _library = {}
        for i in range(count - 1):
            w = BitWriter()
            r = BitReader(rebuild_packed(raw[offsets[i]:offsets[i + 1]]))
            read_codebook(r, w)                     # normalizes the length to the codebook's own bits
            _library.setdefault((w.pos, w.v), i)
    return _library


# ── Setup header ──────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Setup:
    stripped: bytes
    mode_blockflags: list[int]


def strip_setup(packet: bytes, channels: int) -> Setup:
    """Standard Vorbis setup header (spec 4.2.4) -> Wwise stripped setup with external codebooks."""
    if packet[:7] != b"\x05vorbis":
        raise WemError("not a Vorbis setup header")
    r = BitReader(packet[7:])
    w = BitWriter()
    library = codebook_library()

    count = w.copy(r, 8) + 1
    for i in range(count):
        cb = BitWriter()
        read_codebook(r, cb)
        idx = library.get((cb.pos, cb.v))
        if idx is None:
            raise WemError(f"codebook {i} is not in Wwise's library (the Ogg must be encoded with aoTuV b6.03)")
        w.write(idx, 10)

    for _ in range(r.read(6) + 1):                  # time domain transforms: placeholders, not stored
        if r.read(16) != 0:
            raise WemError("bad time domain value")

    floors = w.copy(r, 6) + 1
    for _ in range(floors):
        if r.read(16) != 1:
            raise WemError("only floor type 1 is supported")
        partitions = w.copy(r, 5)
        classes = [w.copy(r, 4) for _ in range(partitions)]
        dims = []
        for _ in range(max(classes) + 1 if classes else 0):
            dims.append(w.copy(r, 3) + 1)
            sub = w.copy(r, 2)
            if sub:
                w.copy(r, 8)
            for _ in range(1 << sub):
                w.copy(r, 8)
        w.copy(r, 2)                                # multiplier
        rangebits = w.copy(r, 4)
        for c in classes:
            for _ in range(dims[c]):
                w.copy(r, rangebits)

    residues = w.copy(r, 6) + 1
    for _ in range(residues):
        rtype = r.read(16)
        if rtype > 2:
            raise WemError(f"bad residue type {rtype}")
        w.write(rtype, 2)
        w.copy(r, 24)
        w.copy(r, 24)
        w.copy(r, 24)
        classifications = w.copy(r, 6) + 1
        w.copy(r, 8)
        cascade = []
        for _ in range(classifications):
            low = w.copy(r, 3)
            high = w.copy(r, 5) if w.copy(r, 1) else 0
            cascade.append(high << 3 | low)
        for c in cascade:
            for bit in range(8):
                if c >> bit & 1:
                    w.copy(r, 8)

    mappings = w.copy(r, 6) + 1
    for _ in range(mappings):
        if r.read(16) != 0:
            raise WemError("only mapping type 0 is supported")
        submaps = w.copy(r, 4) + 1 if w.copy(r, 1) else 1
        if w.copy(r, 1):                            # square polar
            for _ in range(w.copy(r, 8) + 1):
                w.copy(r, ilog(channels - 1))
                w.copy(r, ilog(channels - 1))
        if w.copy(r, 2) != 0:
            raise WemError("bad mapping reserved field")
        if submaps > 1:
            for _ in range(channels):
                w.copy(r, 4)
        for _ in range(submaps):
            w.copy(r, 8)
            w.copy(r, 8)
            w.copy(r, 8)

    modes = w.copy(r, 6) + 1
    blockflags = []
    for _ in range(modes):
        blockflags.append(w.copy(r, 1))
        if r.read(16) != 0 or r.read(16) != 0:
            raise WemError("bad mode window/transform type")
        w.copy(r, 8)
    if r.read(1) != 1:
        raise WemError("missing setup framing bit")
    return Setup(w.bytes(), blockflags)


# ── Ogg ───────────────────────────────────────────────────────────────────────────────────────────────

def ogg_packets(data: bytes) -> tuple[list[bytes], int]:
    """Packets of a single logical Ogg stream and the final granule position."""
    packets, cur, pos, granule = [], b"", 0, 0
    while pos < len(data):
        if data[pos:pos + 4] != b"OggS":
            raise WemError("bad Ogg page")
        granule_pos = struct.unpack_from("<q", data, pos + 6)[0]
        nseg = data[pos + 26]
        lacing = data[pos + 27:pos + 27 + nseg]
        body = pos + 27 + nseg
        for lace in lacing:
            cur += data[body:body + lace]
            body += lace
            if lace < 255:
                packets.append(cur)
                cur = b""
        if granule_pos >= 0:
            granule = granule_pos
        pos = body
    return packets, granule


# ── .wem ──────────────────────────────────────────────────────────────────────────────────────────────

def modify_audio_packet(p: bytes, mode_bits: int, blockflags: list[int]) -> tuple[bytes, int]:
    """Removes the packet-type bit and, in long blocks, the window flags. Returns (packet, mode)."""
    v = int.from_bytes(p, "little")
    if v & 1:
        raise WemError("audio packet with the packet-type bit set")
    mode = (v >> 1) & ((1 << mode_bits) - 1)
    removed = 1 + (2 if blockflags[mode] else 0)
    rest = v >> (1 + mode_bits + (removed - 1))
    nbits = len(p) * 8 - removed
    out = mode | rest << mode_bits
    return out.to_bytes((nbits + 7) // 8, "little"), mode


def ogg_to_wem(ogg: bytes) -> bytes:
    packets, total_samples = ogg_packets(ogg)
    if len(packets) < 4:
        raise WemError("Ogg stream without audio")
    ident = packets[0]
    if ident[:7] != b"\x01vorbis":
        raise WemError("not a Vorbis stream")
    channels = ident[11]
    rate = struct.unpack_from("<I", ident, 12)[0]
    bs0, bs1 = ident[28] & 0x0F, ident[28] >> 4
    setup = strip_setup(packets[2], channels)
    mode_bits = ilog(len(setup.mode_blockflags) - 1)

    audio, decoded, prev, sizes = [], 0, None, []
    for p in packets[3:]:
        if not p:
            continue                                # zero-length packets carry no audio
        m, mode = modify_audio_packet(p, mode_bits, setup.mode_blockflags)
        cur = 1 << (bs1 if setup.mode_blockflags[mode] else bs0)
        samples = 0 if prev is None else prev // 4 + cur // 4
        prev = cur
        decoded += samples
        audio.append(m)
        sizes.append(samples)
    if any(len(a) > 0xFFFF for a in audio):
        raise WemError("audio packet too large for a .wem")

    setup_pkt = struct.pack("<H", len(setup.stripped)) + setup.stripped

    # Seek table, as Wwise writes it (checked byte for byte on 5 files): when the frames since the previous entry
    # reach SEEK_GRANULARITY (counting the current packet), an entry (those frames, offset of the current packet's
    # start minus the previous entry's), with offsets counted from the setup packet. No entry for the remainder.
    seek, frames_acc, pos, last = [], 0, len(setup_pkt), 0
    for a, s in zip(audio, sizes):
        frames_acc += s
        if frames_acc >= SEEK_GRANULARITY:
            seek.append((frames_acc, pos - last))
            last, frames_acc = pos, 0
        pos += 2 + len(a)
    if any(f > 0xFFFF or b > 0xFFFF for f, b in seek):
        raise WemError("seek table entry does not fit in 16 bits")
    seek_bytes = b"".join(struct.pack("<HH", f, b) for f, b in seek)

    audio_bytes = b"".join(struct.pack("<H", len(a)) + a for a in audio)
    data = seek_bytes + setup_pkt + audio_bytes

    first_audio = len(setup_pkt)
    audio_end = len(setup_pkt) + len(audio_bytes)
    last_extra = decoded - total_samples
    if not 0 <= last_extra <= 0xFFFF:
        raise WemError(f"unexpected sample count ({decoded} decoded vs {total_samples})")
    avg_bps = (len(data) - len(seek_bytes)) * rate // total_samples
    vorb = struct.pack("<IIIHHIIHHIIIBB",
                       total_samples, first_audio, audio_end, 0, last_extra,
                       len(seek_bytes), len(seek_bytes) + first_audio,
                       max(len(a) for a in audio), last_extra,
                       DECODE_ALLOC, DECODE_X64_ALLOC, codebook_hash(setup.stripped), bs0, bs1)
    mask = {1: 0x4, 2: 0x3}.get(channels, (1 << channels) - 1)
    fmt = struct.pack("<HHIIHHHHI", 0xFFFF, channels, rate, avg_bps, 0, 0, 0x30, 0, mask) + vorb
    body = b"WAVE" + chunk(b"fmt ", fmt) + chunk(b"data", data)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def chunk(cid: bytes, payload: bytes) -> bytes:
    """RIFF chunk. No pad byte after odd-sized payloads: Wwise does not write it (and ww2ogg rejects it)."""
    return cid + struct.pack("<I", len(payload)) + payload


def codebook_hash(stripped_setup: bytes) -> int:
    """uHashCodebook: 32-bit FNV-1 of the stripped setup packet (checked against Wwise files at 48 and 44.1 kHz)."""
    h = 0x811C9DC5
    for byte in stripped_setup:
        h = (h * 0x01000193) & 0xFFFFFFFF
        h ^= byte
    return h


# ── Reading a .wem (for comparisons) ──────────────────────────────────────────────────────────────────

def riff_chunks(b: bytes) -> dict[str, tuple[int, int]]:
    if b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        raise WemError("not a RIFF/WAVE file")
    i, out = 12, {}
    while i + 8 <= len(b):
        cid = b[i:i + 4].decode("latin-1")
        size = struct.unpack_from("<I", b, i + 4)[0]
        out[cid] = (i + 8, size)
        i += 8 + size + (size & 1)
    return out


def read_wem(b: bytes) -> dict:
    c = riff_chunks(b)
    fo, _ = c["fmt "]
    tag, ch, rate, avg_bps, _, _, cb, ext, mask = struct.unpack_from("<HHIIHHHHI", b, fo)
    names = ("samples", "loop_start", "loop_end", "loop_begin_extra", "loop_end_extra", "seek_size", "data_offset",
             "max_packet", "last_granule_extra", "alloc", "alloc_x64", "codebook_hash", "bs0", "bs1")
    vorb = dict(zip(names, struct.unpack_from("<IIIHHIIHHIIIBB", b, fo + 0x18)))
    do, ds = c["data"]
    data = b[do:do + ds]
    seek = [struct.unpack_from("<HH", data, 4 * k) for k in range(vorb["seek_size"] // 4)]
    pos = vorb["seek_size"]
    n = struct.unpack_from("<H", data, pos)[0]
    setup = data[pos + 2:pos + 2 + n]
    pos = vorb["data_offset"]
    packets = []
    while pos + 2 <= len(data):
        n = struct.unpack_from("<H", data, pos)[0]
        packets.append(data[pos + 2:pos + 2 + n])
        pos += 2 + n
    return dict(chunks={k: v[1] for k, v in c.items()}, tag=tag, channels=ch, rate=rate, avg_bps=avg_bps,
                cb_size=cb, ext=ext, mask=mask, vorb=vorb, seek=seek, setup=setup, packets=packets)


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "--info":
        for path in argv[1:]:
            w = read_wem(Path(path).read_bytes())
            print(path)
            print(f"  chunks={w['chunks']} ch={w['channels']} rate={w['rate']} avg_bps={w['avg_bps']} mask={w['mask']:#x}")
            print(f"  vorb={w['vorb']}")
            print(f"  seek: {len(w['seek'])} entries, first {w['seek'][:3]}, last {w['seek'][-2:]}")
            print(f"  setup {len(w['setup'])} bytes, {len(w['packets'])} audio packets")
        return 0
    if len(argv) != 2:
        print(__doc__)
        return 2
    Path(argv[1]).write_bytes(ogg_to_wem(Path(argv[0]).read_bytes()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

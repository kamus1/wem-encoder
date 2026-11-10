/* PCM WAV (16-bit) -> Ogg Vorbis with aoTuV, VBR.
 *
 * Usage: encode in.wav out.ogg [quality]   (quality like Wwise's QualityFactor: 0-10, default 4)
 *
 * The Wwise .wem built from the Ogg stream relies on aoTuV b6.03's static codebooks,
 * so this must be linked against aoTuV, not stock libvorbis.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vorbis/vorbisenc.h>
#ifdef _WIN32
#include <windows.h>
#include <shellapi.h>
#endif

/* fopen(argv[i]). On Windows argv is in the ANSI code page, so paths with characters outside it
 * (accents, ñ, …) would not open: take the UTF-16 command line instead. */
static FILE *open_arg(char **argv, int i, const wchar_t *wmode, const char *mode) {
#ifdef _WIN32
    int n;
    wchar_t **w = CommandLineToArgvW(GetCommandLineW(), &n);
    FILE *f = w && i < n ? _wfopen(w[i], wmode) : NULL;
    LocalFree(w);
    (void)argv; (void)mode;
    return f;
#else
    (void)wmode;
    return fopen(argv[i], mode);
#endif
}

static uint32_t rd32(const unsigned char *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static uint16_t rd16(const unsigned char *p) { return p[0] | p[1] << 8; }

static void die(const char *msg) { fprintf(stderr, "encode: %s\n", msg); exit(1); }

static void write_pages(ogg_stream_state *os, FILE *out, int flush) {
    ogg_page og;
    while (flush ? ogg_stream_flush(os, &og) : ogg_stream_pageout(os, &og)) {
        fwrite(og.header, 1, og.header_len, out);
        fwrite(og.body, 1, og.body_len, out);
    }
}

int main(int argc, char **argv) {
    if (argc < 3) die("usage: encode in.wav out.ogg [quality 0-10]");
    float quality = argc > 3 ? (float)atof(argv[3]) : 4.0f;

    FILE *in = open_arg(argv, 1, L"rb", "rb");
    if (!in) die("cannot open input");
    unsigned char hdr[12];
    if (fread(hdr, 1, 12, in) != 12 || memcmp(hdr, "RIFF", 4) || memcmp(hdr + 8, "WAVE", 4)) die("not a RIFF/WAVE file");

    int channels = 0, rate = 0, bits = 0;
    long data_size = -1;
    unsigned char ch[8];
    while (fread(ch, 1, 8, in) == 8) {
        uint32_t size = rd32(ch + 4);
        if (!memcmp(ch, "fmt ", 4)) {
            unsigned char f[40];
            if (size < 16 || size > sizeof f || fread(f, 1, size, in) != size) die("bad fmt chunk");
            uint16_t tag = rd16(f);
            if (tag != 1 && !(tag == 0xFFFE && size >= 40)) die("only integer PCM is supported");
            channels = rd16(f + 2); rate = (int)rd32(f + 4); bits = rd16(f + 14);
            if (size & 1) fgetc(in);
        } else if (!memcmp(ch, "data", 4)) {
            data_size = size;
            break;
        } else {
            fseek(in, size + (size & 1), SEEK_CUR);
        }
    }
    if (data_size < 0 || channels < 1 || channels > 8 || bits != 16) die("need a 16-bit PCM WAV with a data chunk");

    vorbis_info vi;
    vorbis_info_init(&vi);
    if (vorbis_encode_init_vbr(&vi, channels, rate, quality / 10.0f)) die("unsupported encoder settings");

    vorbis_comment vc;
    vorbis_comment_init(&vc);
    vorbis_dsp_state vd;
    vorbis_block vb;
    vorbis_analysis_init(&vd, &vi);
    vorbis_block_init(&vd, &vb);

    ogg_stream_state os;
    ogg_stream_init(&os, 1);  /* fixed serial: deterministic output */

    FILE *out = open_arg(argv, 2, L"wb", "wb");
    if (!out) die("cannot open output");
    ogg_packet h_id, h_comm, h_code;
    vorbis_analysis_headerout(&vd, &vc, &h_id, &h_comm, &h_code);
    ogg_stream_packetin(&os, &h_id);
    ogg_stream_packetin(&os, &h_comm);
    ogg_stream_packetin(&os, &h_code);
    write_pages(&os, out, 1);

    enum { FRAMES = 1024 };
    unsigned char *buf = malloc((size_t)FRAMES * channels * 2);
    long left = data_size / (channels * 2);
    int eos = 0;
    while (!eos) {
        long n = left < FRAMES ? left : FRAMES;
        if (n > 0 && fread(buf, (size_t)channels * 2, (size_t)n, in) != (size_t)n) die("truncated data chunk");
        left -= n;
        if (n == 0) {
            vorbis_analysis_wrote(&vd, 0);
        } else {
            float **pcm = vorbis_analysis_buffer(&vd, (int)n);
            for (long i = 0; i < n; i++)
                for (int c = 0; c < channels; c++)
                    pcm[c][i] = (int16_t)rd16(buf + 2 * (i * channels + c)) / 32768.0f;
            vorbis_analysis_wrote(&vd, (int)n);
        }
        while (vorbis_analysis_blockout(&vd, &vb) == 1) {
            vorbis_analysis(&vb, NULL);
            vorbis_bitrate_addblock(&vb);
            ogg_packet op;
            while (vorbis_bitrate_flushpacket(&vd, &op)) {
                ogg_stream_packetin(&os, &op);
                write_pages(&os, out, 0);
                if (op.e_o_s) eos = 1;
            }
        }
    }
    write_pages(&os, out, 1);

    ogg_stream_clear(&os);
    vorbis_block_clear(&vb);
    vorbis_dsp_clear(&vd);
    vorbis_comment_clear(&vc);
    vorbis_info_clear(&vi);
    free(buf);
    fclose(out);
    fclose(in);
    return 0;
}

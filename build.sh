#!/bin/sh
# Builds the encoder against a local static aoTuV b6.03 (downloaded and compiled into build/ if missing).
#   sh build.sh            -> build/encode      (this machine)
#   sh build.sh --windows  -> build/encode.exe  (Windows x64, cross-compiled with MinGW-w64; static, no DLLs)
set -e
ROOT=$(cd "$(dirname "$0")" && pwd)
W="$ROOT/build"
AOTUV_VER=beta6.03-2020
AOTUV_SHA=b529eb656a28cdd3a3ce22fe470a2f7705a088a87ccabe6675f1748e843aef0d
OGG_VER=1.3.6
OGG_SHA=83e6704730683d004d20e21b8f7f55dcb3383cdf84c0daedf30bde175f774638

fetch() {  # url file sha256
    mkdir -p "$W/src"
    [ -f "$W/src/$2" ] || curl -sL -o "$W/src/$2" "$1"
    echo "$3  $W/src/$2" | sha256sum -c - >/dev/null
}

# configure + make install of a source tarball into $prefix (extra configure args after the prefix)
build_lib() {  # tarball dir prefix [configure args...]
    tarball=$1 dir=$2 prefix=$3
    shift 3
    rm -rf "$W/src/$dir.build" && mkdir -p "$W/src/$dir.build"
    tar xzf "$W/src/$tarball" -C "$W/src/$dir.build"
    cd "$W/src/$dir.build/$dir"
    chmod +x configure
    ./configure --prefix="$prefix" --disable-shared --enable-static -q "$@"
    make -j"$(nproc)" -s
    make install -s
    cd "$ROOT"
}

fetch "https://github.com/AO-Yumi/vorbis_aotuv/archive/refs/tags/$AOTUV_VER.tar.gz" aotuv.tar.gz "$AOTUV_SHA"

if [ "$1" = "--windows" ]; then
    HOST=x86_64-w64-mingw32
    P="$W/win"
    command -v $HOST-gcc >/dev/null || { echo "needs $HOST-gcc (Arch: pacman -S mingw-w64-gcc)" >&2; exit 1; }
    if [ ! -f "$P/lib/libogg.a" ]; then
        fetch "https://github.com/xiph/ogg/releases/download/v$OGG_VER/libogg-$OGG_VER.tar.gz" libogg.tar.gz "$OGG_SHA"
        build_lib libogg.tar.gz "libogg-$OGG_VER" "$P" --host=$HOST
    fi
    if [ ! -f "$P/lib/libvorbisenc.a" ]; then
        build_lib aotuv.tar.gz "vorbis_aotuv-$AOTUV_VER" "$P" --host=$HOST \
            OGG_CFLAGS="-I$P/include" OGG_LIBS="-L$P/lib -logg" --disable-docs --disable-examples
    fi
    $HOST-gcc -O2 -Wall -static -o "$W/encode.exe" "$ROOT/encode.c" -I"$P/include" \
        "$P/lib/libvorbisenc.a" "$P/lib/libvorbis.a" "$P/lib/libogg.a" -lshell32 -lm
    echo "$W/encode.exe"
    exit 0
fi

if [ ! -f "$W/aotuv/lib/libvorbisenc.a" ]; then
    build_lib aotuv.tar.gz "vorbis_aotuv-$AOTUV_VER" "$W/aotuv" --disable-docs --disable-examples
fi
cc -O2 -Wall -o "$W/encode" "$ROOT/encode.c" -I"$W/aotuv/include" \
    "$W/aotuv/lib/libvorbisenc.a" "$W/aotuv/lib/libvorbis.a" -logg -lm
echo "$W/encode"

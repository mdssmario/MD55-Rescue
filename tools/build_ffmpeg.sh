#!/usr/bin/env bash
# Compila un ffmpeg mínimo y solo LGPL (sin x264/x265 ni nada GPL) para incluirlo en la
# aplicación: decodifica XAVC (H.264 / H.265), lee MP4 / MXF y escribe JPG y MOV.
# Uso: tools/build_ffmpeg.sh <carpeta de salida>   (Windows: desde MSYS2 MINGW64)
set -euo pipefail

VERSION=7.1.2
OUT="$(mkdir -p "${1:-bin}" && cd "${1:-bin}" && pwd)"
WORK="${TMPDIR:-/tmp}/ffmpeg-build"
rm -rf "$WORK" && mkdir -p "$WORK" && cd "$WORK"
# fuente oficial; si falla (a veces corta la conexión), la copia del mismo código en GitHub
mkdir src
if curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 \
        "https://ffmpeg.org/releases/ffmpeg-$VERSION.tar.xz" -o ffmpeg.tar.xz; then
  tar xf ffmpeg.tar.xz -C src --strip-components=1
else
  curl -fsSL --retry 5 --retry-all-errors --retry-delay 5 \
       "https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n$VERSION.tar.gz" -o ffmpeg.tar.gz
  tar xzf ffmpeg.tar.gz -C src --strip-components=1
fi
cd src

FLAGS=(
  --disable-everything --disable-autodetect --disable-network --disable-doc --disable-debug
  --disable-ffprobe --disable-ffplay --disable-shared --enable-static
  --enable-swscale --enable-swresample
  --enable-protocol=file,pipe
  --enable-demuxer=mov,mxf,h264,hevc,wav,image2
  --enable-muxer=null,mov,image2,mjpeg
  --enable-decoder=h264,hevc,mjpeg,pcm_s16be,pcm_s16le,pcm_s24be,pcm_s24le,pcm_s32le
  --enable-encoder=mjpeg,wrapped_avframe,pcm_s16le,pcm_s24le
  --enable-parser=h264,hevc,mjpeg
  --enable-bsf=h264_mp4toannexb,hevc_mp4toannexb
  --enable-filter=scale,format,null,anull,aresample
)
case "$(uname -s)" in
  MINGW*|MSYS*)
    FLAGS+=(--enable-w32threads --disable-pthreads --extra-ldflags=-static --pkg-config-flags=--static)
    EXE=ffmpeg.exe ;;
  Darwin)
    export MACOSX_DEPLOYMENT_TARGET=11.0
    FLAGS+=(--enable-pthreads --extra-cflags=-mmacosx-version-min=11.0
            --extra-ldflags=-mmacosx-version-min=11.0)
    EXE=ffmpeg ;;
  *)
    FLAGS+=(--enable-pthreads)
    EXE=ffmpeg ;;
esac

./configure "${FLAGS[@]}"
make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" ffmpeg
strip "$EXE" 2>/dev/null || true
cp "$EXE" "$OUT/"
cp COPYING.LGPLv2.1 "$OUT/FFMPEG-LICENSE.txt"
cat > "$OUT/FFMPEG-README.txt" <<EOF
MD55 Rescue incluye ffmpeg $VERSION (https://ffmpeg.org), con licencia LGPL v2.1 o posterior
(ver FFMPEG-LICENSE.txt). Se usa como programa aparte para verificar los clips reparados y
sacar fotogramas de vista previa.

Código fuente: https://ffmpeg.org/releases/ffmpeg-$VERSION.tar.xz
Compilado sin modificaciones con: ./configure ${FLAGS[*]}
EOF
"$OUT/$EXE" -hide_banner -version | head -1

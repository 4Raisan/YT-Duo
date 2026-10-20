#!/usr/bin/env bash
# Run in MSYS2 MINGW64 after prepare_ffmpeg_source(). No general-purpose build.
set -euo pipefail
project="$(pwd)"
source_dir="$(python -c 'from scripts.build_release import ffmpeg_source_dir; print(ffmpeg_source_dir().as_posix())')"
output="$project/build/minimal-ffmpeg"
mkdir -p "$output/licenses/ffmpeg"
cd "$source_dir"
# An archive inside another Git checkout must not inherit that repo's version.
python -c 'import json; from pathlib import Path; p=Path("'"$project"'/scripts/windows-tools.json"); Path("VERSION").write_text(json.loads(p.read_text())[0]["version"] + "\n")'
./configure \
  --extra-version=YT-Duo-minimal \
  --target-os=mingw32 --arch=x86_64 --cc=gcc --cxx=g++ \
  --disable-autodetect --disable-everything --disable-network \
  --disable-doc --disable-debug --disable-ffplay --disable-shared \
  --enable-static --enable-small --optflags=-Os --extra-ldflags=-static \
  --pkg-config-flags=--static --enable-ffmpeg --enable-ffprobe \
  --enable-avcodec --enable-avformat --enable-avfilter --enable-swresample \
  --enable-libmp3lame \
  --enable-protocol=file,pipe \
  --enable-demuxer=mov,matroska,mpegts,aac,mp3,ogg,flac,wav \
  --enable-muxer=mp4,mov,ipod,matroska,webm,mp3,ogg,opus,adts,flac,wav \
  --enable-decoder=aac,aac_fixed,mp3,mp3float,opus,vorbis,flac,alac,pcm_s16le,pcm_s24le,pcm_s32le,pcm_f32le,h264,hevc,vp8,vp9,av1 \
  --enable-encoder=libmp3lame,pcm_s16le \
  --enable-parser=aac,aac_latm,mpegaudio,opus,vorbis,flac,h264,hevc,av1,vp9 \
  --enable-bsf=aac_adtstoasc,h264_mp4toannexb,hevc_mp4toannexb,extract_extradata,vp9_superframe \
  --enable-filter=aresample,aformat,anull
make -j"$(nproc)" ffmpeg.exe ffprobe.exe
cp ffmpeg.exe ffprobe.exe "$output/"
cp LICENSE.md COPYING.LGPLv2.1 "$output/licenses/ffmpeg/"
cp ffbuild/config.log "$output/licenses/ffmpeg/config.log"
pacman -Q > "$output/licenses/ffmpeg/build-packages.txt"
test -s "$output/licenses/ffmpeg/LAME-COPYING"
cd "$project"
python -c 'import json; from pathlib import Path; p=Path("scripts/windows-tools.json"); Path("build/minimal-ffmpeg/source-revision.txt").write_text(json.loads(p.read_text())[0]["commit"] + "\n")'
ls -lh "$output/"*.exe

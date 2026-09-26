#!/bin/bash
# Generate a tiny tagged music library (3 albums x 2 tracks, 4 s sine tones,
# ID3v2.3 with album artist, 300x300 cover.jpg) for the simulator tests.
#   make_fixtures.sh <output Music dir>
set -euo pipefail
OUT="${1:?usage: make_fixtures.sh <output dir>}"
command -v ffmpeg >/dev/null || { echo "ffmpeg required"; exit 1; }
rm -rf "$OUT"; mkdir -p "$OUT"
i=0
for album in "Red Album|Red Band|ff0000" "Blue Album|Blue Band|0000ff" "Green Album|Green Band|00aa00"; do
    IFS='|' read -r name artist color <<< "$album"
    d="$OUT/$artist - $name"; mkdir -p "$d"
    ffmpeg -loglevel error -y -f lavfi -i "color=c=0x$color:s=300x300:d=1" -frames:v 1 -q:v 3 "$d/cover.jpg"
    for t in 1 2; do
        ffmpeg -loglevel error -y -f lavfi -i "sine=frequency=$((300+i*100)):duration=4" \
            -ac 2 -ar 44100 -b:a 64k -id3v2_version 3 \
            -metadata title="Track $t" -metadata artist="$artist" -metadata album_artist="$artist" \
            -metadata album="$name" -metadata track="$t" "$d/0$t - Track $t.mp3"
        i=$((i+1))
    done
done
echo "fixtures in $OUT"

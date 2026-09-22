#!/usr/bin/env bash
# Build the niko-ssg docs site.
#
# Writes the page manifest (one source path per line, relative to the
# repository root), then runs the generator (examples/ssg/ssg.niko --
# pure Niko, no Python shelling out) which renders every page into
# examples/ssg/site/.
#
# Usage: ./build.sh   (run from examples/ssg/)

set -e
cd "$(dirname "$0")"

ROOT="../.."
{
    echo "STDLIB.md"
    echo "NIKO_AI_BRIEF.md"
    for f in "$ROOT"/RELEASE_NOTES_ALPHA*.md; do
        echo "${f#$ROOT/}"
    done
} > pages.txt

mkdir -p site

cd "$ROOT"
python3 -m niko2 run examples/ssg/ssg.niko

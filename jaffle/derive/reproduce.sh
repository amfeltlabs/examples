#!/bin/sh
# Reproduce the Jaffle derivation chain from the archived generation run, and fail loudly
# on any deviation. jafgen is never run: the chain starts from the archive.
#
# Usage: sh jaffle/derive/reproduce.sh <path to jaffle-fullyear-jafgen-0.4.14.tar.gz>
#
# Download the archive from the release corpus/jaffle-fullyear-2026-09-24 of this
# repository, for example:
#   gh release download corpus/jaffle-fullyear-2026-09-24 -R <owner>/examples \
#     -p jaffle-fullyear-jafgen-0.4.14.tar.gz
#
# Steps, each of which must pass before the next runs:
#   1. the archive's SHA-256 matches ARCHIVE_SHA256, checked before anything is extracted;
#   2. derive/trim.py turns the full-year files into the trimmed corpus;
#   3. the trimmed corpus matches derive/trim.SHA256SUMS.
# Later steps (shift, addresses, pinned data) append below the marked line.
set -eu

# The SHA-256 of the archive, as recorded in PROVENANCE.
ARCHIVE_SHA256=bd37c5d94a332ea93f6e24ce4f5b5f94b70f189f4ec5e31e6f32b3333e15568b

fail() {
    printf 'reproduce.sh: FAIL: %s\n' "$*" >&2
    exit 1
}

if [ "$#" -ne 1 ]; then
    printf 'usage: sh reproduce.sh <jaffle-fullyear-jafgen-0.4.14.tar.gz>\n' >&2
    exit 2
fi
archive=$1
[ -f "$archive" ] || fail "archive not found: $archive"

here=$(cd "$(dirname "$0")" && pwd)

if command -v sha256sum >/dev/null 2>&1; then
    sha256() { sha256sum "$@"; }
else
    sha256() { shasum -a 256 "$@"; }
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM

# 1. Verify the archive before extracting it.
got=$(sha256 "$archive" | cut -d ' ' -f 1)
[ "$got" = "$ARCHIVE_SHA256" ] || fail "archive sha256 is $got, PROVENANCE records $ARCHIVE_SHA256"
printf 'ok: archive sha256 %s\n' "$got"

mkdir "$work/archive"
tar -xzf "$archive" -C "$work/archive"
fullyear="$work/archive/jaffle-fullyear"
[ -d "$fullyear" ] || fail "the archive holds no jaffle-fullyear directory"

# 2. Trim. trim.py checks every input file against the full-year digests itself.
python3 "$here/trim.py" "$fullyear" "$work/trim"

# 3. Compare the trimmed corpus with its pinned digests; any mismatch fails.
(cd "$work/trim" && sha256 -c "$here/trim.SHA256SUMS") || fail "trim output differs from trim.SHA256SUMS"
extra=$(find "$work/trim" -type f | wc -l | tr -d ' ')
want=$(wc -l <"$here/trim.SHA256SUMS" | tr -d ' ')
[ "$extra" = "$want" ] || fail "trim wrote $extra files, trim.SHA256SUMS lists $want"
printf 'ok: trim output matches trim.SHA256SUMS\n'

# --- later items append their steps here (shift.py, addresses.py, data/ SHA256SUMS) ---

printf 'reproduce.sh: all checks passed\n'

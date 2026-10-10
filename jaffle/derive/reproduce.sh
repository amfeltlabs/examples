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
#   3. the trimmed corpus matches derive/trim.SHA256SUMS;
#   4. derive/shift.py's unit tests pass on it, and its published files match the committed
#      jaffle/data/ and jaffle/SHA256SUMS.
# Later steps (addresses, pinned data) append below the marked line.
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

# Compare a directory with a sums file, exactly: every line of the sums file is
# well-formed, the names it lists (duplicates kept) are the files the directory holds, and
# every file matches its digest. sha256sum alone only warns about a malformed line.
# Usage: check_sums <dir> <sums file> <label>
check_sums() {
    bad=$(grep -Evc '^[0-9a-f]{64}  [A-Za-z0-9_.-]+$' "$2" || true)
    [ "$bad" = 0 ] || fail "$3 sums file has $bad malformed line(s)"
    cut -c 67- "$2" | sort >"$work/names.want"
    (cd "$1" && find . -type f | sed 's#^\./##' | sort) >"$work/names.got"
    cmp -s "$work/names.want" "$work/names.got" || fail "files in $2 differ from the files in the $3 output"
    (cd "$1" && sha256 -c "$2") || fail "$3 output differs from $2"
}

# The script digests PROVENANCE records must be the scripts' actual digests, so an edit
# to a script cannot leave a stale record behind.
check_script() {
    want=$(sed -n "s/^$2=//p" "$here/../PROVENANCE")
    got=$(sha256 "$here/$1" | cut -d ' ' -f 1)
    [ "$want" = "$got" ] || fail "$1 sha256 is $got, PROVENANCE records ${want:-nothing} as $2"
}
check_script trim.py TRIM_SHA256
check_script shift.py SHIFT_SHA256
printf 'ok: PROVENANCE records the digests of trim.py and shift.py\n'

# 3. Compare the trimmed corpus with its pinned digests.
check_sums "$work/trim" "$here/trim.SHA256SUMS" trim
printf 'ok: trim output matches trim.SHA256SUMS\n'

# 4. Shift. shift.py checks the trim output against its digests itself. Its unit tests run
# on the same trim output. The published files must match the committed jaffle/data/ and
# its SHA256SUMS exactly.
JAFFLE_TRIM_DIR="$work/trim" python3 -m unittest discover -s "$here" -t "$here" \
    || fail "shift.py unit tests failed"
python3 "$here/shift.py" "$work/trim" "$work/data"
check_sums "$work/data" "$here/../SHA256SUMS" shift
(cd "$work/data" && for f in *; do cmp -s "$f" "$here/../data/$f" || exit 1; done) \
    || fail "shift output differs from the committed jaffle/data/"
(cd "$here/../data" && find . -type f | sort) >"$work/data.committed"
(cd "$work/data" && find . -type f | sort) >"$work/data.shifted"
cmp -s "$work/data.committed" "$work/data.shifted" \
    || fail "jaffle/data/ holds files the shift did not write"
printf 'ok: shift output matches jaffle/SHA256SUMS and jaffle/data/\n'

# --- later items append their steps here (addresses.py, data/ SHA256SUMS) ---

printf 'reproduce.sh: all checks passed\n'

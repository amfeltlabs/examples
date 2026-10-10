#!/bin/sh
# Prove that build-asset.sh is byte for byte reproducible, and that check-tag.sh rejects what
# it must.
#
# Usage: sh jaffle/release/test-reproducible.sh
#
# The asset is built twice. The second build differs in everything the script must not
# depend on: the output directory, the timezone, the umask, the source tree's location, and
# its files' mtimes and permission bits. The SHA-256 of both tarballs must match. The archive
# is then read back: the root, the owner and group, the mtime and the sort order. Needs GNU
# tar (gtar on macOS).
set -eu

fail() {
    printf 'test-reproducible.sh: FAIL: %s\n' "$*" >&2
    exit 1
}

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/jaffle-repro.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM

if command -v gtar >/dev/null 2>&1; then tar=gtar; else tar=tar; fi

if command -v sha256sum >/dev/null 2>&1; then
    sha256() { sha256sum "$@"; }
else
    sha256() { shasum -a 256 "$@"; }
fi

epoch=${SOURCE_DATE_EPOCH:-$(git -C "$root" log -1 --format=%ct)}
TAG=jaffle/v9.9.9-rc.1

# Build 1: the checkout itself.
(umask 022 && TZ=UTC SOURCE_DATE_EPOCH=$epoch sh "$here/build-asset.sh" "$TAG" "$work/out-a") >/dev/null

# Build 2: a copy with scrambled mtimes and modes, built far away under another zone and umask.
mkdir -p "$work/copy"
cp -R "$root/jaffle" "$work/copy/jaffle"
find "$work/copy/jaffle" -type f -exec touch -t 200101010101 {} +
find "$work/copy/jaffle" -type f -name '*.csv' -exec chmod 0600 {} +
(umask 077 && TZ=Pacific/Auckland JAFFLE_SOURCE="$work/copy" SOURCE_DATE_EPOCH=$epoch \
    sh "$here/build-asset.sh" "$TAG" "$work/deeper/out-b") >/dev/null

a=$(sha256 "$work/out-a/jaffle-v9.9.9-rc.1.tar.gz" | cut -d' ' -f1)
b=$(sha256 "$work/deeper/out-b/jaffle-v9.9.9-rc.1.tar.gz" | cut -d' ' -f1)
[ "$a" = "$b" ] || fail "the two builds differ: $a != $b"
printf 'ok: both builds are %s\n' "$a"

# The .sha256 file is the standard "<hex>  <name>" line.
[ "$(cat "$work/out-a/jaffle-v9.9.9-rc.1.tar.gz.sha256")" = "$a  jaffle-v9.9.9-rc.1.tar.gz" ] ||
    fail "the .sha256 file is not '<hex>  <name>'"

# Read the archive back.
tarball=$work/out-a/jaffle-v9.9.9-rc.1.tar.gz
"$tar" --list --verbose --numeric-owner --full-time --gzip --file "$tarball" >"$work/listing"
"$tar" --list --gzip --file "$tarball" >"$work/names"
[ "$(head -c 2 "$tarball" | od -An -tx1 | tr -d ' ')" = 1f8b ] || fail "not a gzip file"
# gzip -n: the header's mtime (bytes 4-7) and the name flag (bit 3 of byte 3) are zero.
[ "$(head -c 8 "$tarball" | tail -c 5 | od -An -tx1 | tr -d ' \n')" = 0000000000 ] ||
    fail "the gzip header carries a name or time (gzip -n is missing)"
grep -qv '^jaffle/' "$work/names" && fail "an entry outside jaffle/"
LC_ALL=C sort -c "$work/names" 2>/dev/null || fail "the entries are not sorted by name"
grep -q ' 0/0 ' "$work/listing" || fail "owner and group are not 0/0"
grep -v ' 0/0 ' "$work/listing" && fail "an entry has an owner other than 0/0"
expected_time=$(TZ=UTC date -u -d "@$epoch" '+%Y-%m-%d %H:%M:%S' 2>/dev/null ||
    TZ=UTC date -u -r "$epoch" '+%Y-%m-%d %H:%M:%S')
grep -v " $expected_time" "$work/listing" && fail "an entry's mtime is not $expected_time"
for want in jaffle/data/customers.csv jaffle/db/load.sql jaffle/db/initdb.sh \
    jaffle/db/check_load.sql jaffle/expected/counts.yaml jaffle/SHA256SUMS jaffle/NOTICE \
    jaffle/LICENSE.jaffle-shop-generator; do
    grep -qx "$want" "$work/names" || fail "the archive lacks $want"
done
grep -E '^jaffle/(derive|release)/|PROVENANCE|VERSION|__pycache__' "$work/names" &&
    fail "the archive holds files that are not part of the asset"
echo "ok: root, order, owner, mtime and content list"

# check-tag.sh accepts and rejects.
printf '1.0.0\n' >"$work/VERSION"
[ "$(sh "$here/check-tag.sh" jaffle/v1.0.0 "$work/VERSION")" = release ] || fail "check-tag: release"
[ "$(sh "$here/check-tag.sh" jaffle/v1.0.0-rc.2 "$work/VERSION")" = prerelease ] || fail "check-tag: rc"
for bad in jaffle/v1.0.1 jaffle/v1.1.0-rc.1 v1.0.0 jaffle/v1.0.0-beta.1 jaffle/v1.0.0-rc.0 \
    'jaffle/v1.0.0;x' jaffle/v01.0.0 jaffle/v1.0.0-rc.1x; do
    if sh "$here/check-tag.sh" "$bad" "$work/VERSION" >/dev/null 2>&1; then
        fail "check-tag accepted $bad"
    fi
done
echo "ok: check-tag accepts and rejects"

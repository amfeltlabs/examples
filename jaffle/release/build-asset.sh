#!/bin/sh
# Build the Jaffle release asset, byte for byte reproducibly.
#
# Usage: sh jaffle/release/build-asset.sh <tag> <output dir>
#
#   <tag>         jaffle/v1.0.0-rc.1, v1.0.0-rc.1 or 1.0.0-rc.1; the asset is named
#                 jaffle-v1.0.0-rc.1.tar.gz
#   <output dir>  receives the tarball and jaffle-<version>.tar.gz.sha256
#
# The tarball's root is jaffle/ and holds data/, db/, expected/, SHA256SUMS, NOTICE and
# LICENSE.jaffle-shop-generator: what consumers load. The derivation scripts, PROVENANCE,
# VERSION and release tooling stay in the repository.
#
# Reproducibility: the files are copied to a staging tree with explicit modes (0755 for
# *.sh, 0644 for files, 0755 for directories), so the checkout's umask never reaches the
# archive. GNU tar then writes the entries sorted by name, owned by 0:0 with numeric ids,
# all stamped with one mtime, in the gnu format; gzip -n leaves the name and time out of
# the gzip header. The mtime is SOURCE_DATE_EPOCH when set, otherwise the commit date of
# HEAD, so a tag's commit date fixes it. The timezone, umask and directory the build runs
# in do not matter.
#
# GNU tar is required. On macOS install it with `brew install gnu-tar`; it is then
# available as gtar, which this script prefers. Set JAFFLE_SOURCE to build from another
# copy of the repository root (the reproducibility test does).
set -eu

fail() {
    printf 'build-asset.sh: FAIL: %s\n' "$*" >&2
    exit 1
}

[ $# -eq 2 ] || fail "usage: build-asset.sh <tag> <output dir>"
tag=$1
out=$2

version=${tag#jaffle/}
version=${version#v}
case $version in
    [0-9]*.[0-9]*.[0-9]* ) ;;
    * ) fail "cannot read a version from '$tag'" ;;
esac
name="jaffle-v$version.tar.gz"

here=$(cd "$(dirname "$0")" && pwd)
root=${JAFFLE_SOURCE:-$(cd "$here/../.." && pwd)}
src="$root/jaffle"
[ -d "$src/data" ] || fail "no jaffle/data under $root"

# Locale, timezone and umask are pinned so the sort order and the modes cannot vary.
LC_ALL=C
TZ=UTC
export LC_ALL TZ
umask 022

if command -v gtar >/dev/null 2>&1; then
    tar=gtar
else
    tar=tar
fi
"$tar" --version 2>/dev/null | head -n 1 | grep -q 'GNU tar' ||
    fail "GNU tar is required (macOS: brew install gnu-tar, which provides gtar)"

if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then
    epoch=$SOURCE_DATE_EPOCH
else
    epoch=$(git -C "$root" log -1 --format=%ct 2>/dev/null) ||
        fail "no SOURCE_DATE_EPOCH and no git history in $root"
fi
case $epoch in
    '' | *[!0-9]* ) fail "the mtime '$epoch' is not an epoch in seconds" ;;
esac

if command -v sha256sum >/dev/null 2>&1; then
    sha256() { sha256sum "$@"; }
else
    sha256() { shasum -a 256 "$@"; }
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/jaffle-asset.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM

# The data files must match SHA256SUMS, and SHA256SUMS must list every data file, before
# anything is packed.
(cd "$src/data" && sha256 -c ../SHA256SUMS >/dev/null) ||
    fail "the data files do not match jaffle/SHA256SUMS"
(cd "$src/data" && find . -type f | sed 's|^\./||' | sort) >"$work/on-disk"
sed 's/^[0-9a-f]*  //' "$src/SHA256SUMS" | sort >"$work/listed"
cmp -s "$work/on-disk" "$work/listed" ||
    fail "jaffle/SHA256SUMS and jaffle/data/ list different files"

# Stage exactly the shipped files. __pycache__ and other stray files never get in.
stage="$work/stage"
mkdir -p "$stage/jaffle"
(
    cd "$src"
    {
        find data db -type f
        find expected -type f ! -name '*.pyc' ! -path '*/__pycache__/*'
        printf '%s\n' SHA256SUMS NOTICE LICENSE.jaffle-shop-generator
    } | sort
) >"$work/files"
while IFS= read -r f; do
    [ -f "$src/$f" ] || fail "missing $f"
    mkdir -p "$stage/jaffle/$(dirname "$f")"
    cp "$src/$f" "$stage/jaffle/$f"
    case $f in
        *.sh ) chmod 0755 "$stage/jaffle/$f" ;;
        * ) chmod 0644 "$stage/jaffle/$f" ;;
    esac
done <"$work/files"
find "$stage/jaffle" -type d -exec chmod 0755 {} +

mkdir -p "$out"
"$tar" --create --file "$work/jaffle.tar" --directory "$stage" \
    --sort=name --format=gnu --mtime="@$epoch" \
    --owner=0 --group=0 --numeric-owner \
    jaffle
gzip -n -9 -c "$work/jaffle.tar" >"$work/$name"

# Verify the stage against its own sums once more through the finished archive.
mkdir "$work/check"
"$tar" --extract --gzip --file "$work/$name" --directory "$work/check"
(cd "$work/check/jaffle/data" && sha256 -c ../SHA256SUMS >/dev/null) ||
    fail "the packed data files do not match SHA256SUMS"

cp "$work/$name" "$out/$name"
(cd "$out" && sha256 "$name" >"$name.sha256")
printf 'built %s/%s\n' "$out" "$name"
cat "$out/$name.sha256"

#!/bin/sh
# Check a release tag against jaffle/VERSION.
#
# Usage: sh jaffle/release/check-tag.sh <tag> [VERSION file]
#
# The tag is jaffle/vX.Y.Z or jaffle/vX.Y.Z-rc.N. The version before any -rc.N must equal the
# first line of jaffle/VERSION. Prints "prerelease" or "release" on success.
set -eu

fail() {
    printf 'check-tag.sh: FAIL: %s\n' "$*" >&2
    exit 1
}

[ $# -ge 1 ] || fail "usage: check-tag.sh <tag> [VERSION file]"
tag=$1
file=${2:-$(cd "$(dirname "$0")/.." && pwd)/VERSION}

# The pattern is anchored by case; it admits no other characters, so the tag is safe to echo.
case $tag in
    jaffle/v[0-9]*.[0-9]*.[0-9]* ) ;;
    * ) fail "'$tag' is not jaffle/vX.Y.Z or jaffle/vX.Y.Z-rc.N" ;;
esac
version=${tag#jaffle/v}
printf '%s' "$version" | grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-rc\.[1-9][0-9]*)?$' ||
    fail "'$tag' is not jaffle/vX.Y.Z or jaffle/vX.Y.Z-rc.N"

base=${version%%-rc.*}
[ -f "$file" ] || fail "$file does not exist"
recorded=$(head -n 1 "$file" | tr -d '[:space:]')
[ "$base" = "$recorded" ] ||
    fail "the tag's version is $base but $file says $recorded; bump VERSION by pull request first"

case $version in
    *-rc.* ) echo prerelease ;;
    * ) echo release ;;
esac

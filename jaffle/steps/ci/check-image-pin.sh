#!/bin/sh
# Check that jaffle/.env.example runs the agent image jaffle/project/IMAGE pins: its one
# AGENT_IMAGE line must equal IMAGE's AGENT_IMAGE:AGENT_IMAGE_TAG@AGENT_IMAGE_DIGEST,
# so the tag and the digest a reader copies into .env are the ones the ready-made project
# and the expected counts were made with.
#
# Usage: sh jaffle/steps/ci/check-image-pin.sh [JAFFLE_DIR]
#
# JAFFLE_DIR defaults to the jaffle/ directory this script sits in. Exits 1 on any
# difference, and on a key that is missing or set more than once in either file.
set -eu

dir=${1:-$(dirname "$0")/../..}
env_file=$dir/.env.example
image_file=$dir/project/IMAGE

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

# value FILE KEY: the value of the one uncommented KEY= line in FILE.
value() {
    n=$(grep -c "^$2=" "$1" || true)
    [ "$n" -eq 1 ] || fail "$1 sets $2 on $n lines, not exactly one"
    sed -n "s/^$2=//p" "$1"
}

[ -f "$env_file" ] || fail "$env_file is missing"
[ -f "$image_file" ] || fail "$image_file is missing"

name=$(value "$image_file" AGENT_IMAGE)
tag=$(value "$image_file" AGENT_IMAGE_TAG)
digest=$(value "$image_file" AGENT_IMAGE_DIGEST)
want="$name:$tag@$digest"
got=$(value "$env_file" AGENT_IMAGE)

if [ "$got" != "$want" ]; then
    printf '  project/IMAGE pins  %s\n  .env.example runs   %s\n' "$want" "$got" >&2
    fail ".env.example's AGENT_IMAGE differs from project/IMAGE's image, tag and digest"
fi
echo "ok: .env.example runs $want, the image project/IMAGE pins"

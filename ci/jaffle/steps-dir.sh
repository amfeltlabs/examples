#!/bin/sh
# Print the directory that holds the Jaffle steps of the ref checked out around JAFFLE_DIR.
#
# Usage: sh ci/jaffle/steps-dir.sh JAFFLE_DIR
#
# CI runs the helpers of the triggering commit against a ref that can be older. The steps
# sit in jaffle/steps/ on every tag up to jaffle/v2.4.0 that has them, and in ci/jaffle/steps/
# from then on. A tree with neither stops here, so a step that went missing fails the run
# and skips nothing. fresh-clone.sh fails a ref that holds jaffle/steps and has the reader's
# Compose path, so the old path stays open only to refs from before that one.
set -eu

jaffle=$(cd "$1" && pwd)
for dir in "$jaffle/../ci/jaffle/steps" "$jaffle/steps"; do
    if [ -f "$dir/01-start.sh" ]; then
        (cd "$dir" && pwd)
        exit 0
    fi
done
echo "steps-dir.sh: $jaffle holds no steps, in ../ci/jaffle/steps or in steps" >&2
exit 1

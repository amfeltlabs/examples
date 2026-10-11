#!/bin/sh
# Run the Jaffle tutorial as a reader runs it, and assert what it publishes.
#
# Usage: sh jaffle/steps/ci/tutorial.sh [JAFFLE_DIR]
#
# The steps in JAFFLE_DIR/steps run unchanged, in the reader's order, against the agent
# image .env.example pins. This script adds no step of its own to the tutorial; around
# the steps it asserts, from its own queries:
#
#   - after steps 4 and 6, every warehouse count of that run in expected/counts.yaml, and
#     after step 6 the history reveal: the rows of the mover and of the deleted customer
#     in the history table and in the bridge view of open versions;
#   - a reader re-running run 1's sync: the counts stay the same (plan Review Focus 3);
#   - a reader leaving a DuckDB shell open: the sync fails, and its error output names the
#     lock (Review Focus 4); the warehouse is unchanged afterwards;
#   - the documented reset, steps/99-reset.sh (docker compose down --volumes): step 1 then
#     starts from the freshly loaded source, with no project and no warehouse (Focus 3).
#
# It ends with steps/99-reset.sh, and runs it on any failure as well.
#
# JAFFLE_DIR is the jaffle/ directory whose steps, data and expected counts are tested; it
# defaults to the one this script sits in. CI passes the tree of the ref it tests, so this
# script can test the steps of an earlier commit, such as a release tag.
#
# TUTORIAL_COUNTS names the counts file the assertions read, by default
# JAFFLE_DIR/expected/counts.yaml. A copy with one count changed must turn the run red;
# the steps keep reading their own expected/counts.yaml.
#
# Docker: the compose project is amfelt-jaffle. The script refuses to start while any
# container, volume or network of that project exists, or while work/ or warehouse/
# exists, so everything it removes is something it made.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
jaffle=$(cd "${1:-$here/../..}" && pwd)
counts=${TUTORIAL_COUNTS:-$jaffle/expected/counts.yaml}
if [ "$counts" = "$jaffle/expected/counts.yaml" ]; then
    counts_name=expected/counts.yaml
else
    counts_name=$counts
fi
project=amfelt-jaffle
# Quiet compose progress for this script's own queries, as lib.sh sets it for the steps.
COMPOSE_PROGRESS=${COMPOSE_PROGRESS:-quiet}
export COMPOSE_PROGRESS

say() {
    printf '\n[tutorial-ci] %s\n' "$*"
}

die() {
    printf '[tutorial-ci] FAIL: %s\n' "$*" >&2
    exit 1
}

# get PATH: one value from the counts file under test.
get() {
    python3 "$jaffle/expected/counts.py" get "$1" "$counts"
}

# identifier VALUE: VALUE, if it is a plain SQL identifier; it is put into queries.
identifier() {
    case $1 in
        "" | [!a-z_]* | *[!a-z0-9_]*) die "'$1' from $counts_name is not a plain identifier" ;;
    esac
    printf '%s\n' "$1"
}

# run_step NAME: run one of the reader's steps, unchanged.
run_step() {
    say "step $1"
    "$jaffle/steps/$1"
}

# with_lib SHELL-CODE: run SHELL-CODE with the steps' own lib.sh sourced, as if it were a
# step: \$0 names a file in steps/, so lib.sh moves to jaffle/ and checks .env as it does
# for every step. The code can then use source_url and the other helpers unchanged.
with_lib() {
    # shellcheck disable=SC2016 # expanded by the inner shell
    sh -c 'set -eu; . "$(dirname "$0")/lib.sh"; '"$1" "$jaffle/steps/tutorial-ci"
}

# Run 1's sync, as steps/04-run1.sh spells it.
# shellcheck disable=SC2016 # expanded by with_lib's shell
sync_cmd='url=$(source_url); docker compose run --rm agent sync --job load --source-url "$url" --level warn'

# duckdb SQL: run SQL in the reader's read-only DuckDB shell; one line per row, fields
# separated by |.
duckdb() {
    (cd "$jaffle" && docker compose run --rm -T duckdb -list -noheader -c "$1")
}

# snapshot: one line per warehouse object, "layer object count", in a fixed order.
snapshot() {
    sql=""
    for layer in staging history bridge_act bridge_hst; do
        for object in customers stores orders order_items products supplies tweets; do
            case $layer in
                staging) relation=${prefix}_stg.$object ;;
                history) relation=${prefix}_hst.$object ;;
                bridge_act) relation=${prefix}_bdg.${object}_act ;;
                bridge_hst) relation=${prefix}_bdg.${object}_hst ;;
            esac
            sql="$sql${sql:+ UNION ALL }SELECT '$layer' AS l, '$object' AS o, count(*) AS n FROM $relation"
        done
    done
    # Through a file, not a pipe: a failed query must fail the caller, not reach sort.
    duckdb "SELECT l || ' ' || o || ' ' || n FROM ($sql)" >"$tmp/snapshot.raw"
    sort "$tmp/snapshot.raw"
}

# assert_counts RUN FILE: FILE, a snapshot, holds every count of RUN in the counts file.
assert_counts() {
    run=$1
    total=0
    bad=0
    printf '%-11s %-12s %9s %9s\n' layer object expected actual
    while read -r layer object n; do
        total=$((total + 1))
        want=$(get "runs.$run.$layer.$object")
        mark=""
        if [ "$n" != "$want" ]; then
            mark="  <-- differs"
            bad=$((bad + 1))
        fi
        printf '%-11s %-12s %9s %9s%s\n' "$layer" "$object" "$want" "$n" "$mark"
    done <"$2"
    [ "$total" -eq 28 ] || die "the warehouse returned $total counts, not 28"
    [ "$bad" -eq 0 ] || die "$bad of 28 warehouse counts differ from $counts_name ($run)"
    echo "ok: all 28 warehouse counts equal $counts_name ($run)"
}

# assert_source RUN: the source holds RUN's source counts and the reveal's customers.
assert_source() {
    python3 "$jaffle/expected/counts.py" expect "$1" "$counts" >"$tmp/source.expected"
    (cd "$jaffle" && docker compose exec -T source psql -X -v ON_ERROR_STOP=1 -U jaffle -d jaffle \
        -v mover="$mover" -v deleted="$deleted" -f /jaffle/db/source_counts.sql) >"$tmp/source.actual"
    diff -u "$tmp/source.expected" "$tmp/source.actual" \
        || die "the source differs from $counts_name ($1)"
    echo "ok: the source equals runs.$1.source and the reveal's customers in $counts_name"
}

# address PATH: an address block of the reveal, as the fields the queries print.
address() {
    printf '%s|%s|%s|%s' "$(get "$1.street")" "$(get "$1.postal_code")" \
        "$(get "$1.city")" "$(get "$1.country")"
}

# expect_customer BLOCK: the lines the reveal expects for one customer: per history
# version its name, address and open or closed, and the address bridge_act holds.
expect_customer() {
    name=$(get "$1.name")
    for state in closed open; do
        which=$(get "$1.history.$state")
        if [ "$which" != none ]; then
            printf 'history|%s|%s|%s\n' "$name" "$(address "$1.$which")" "$state"
        fi
    done
    which=$(get "$1.bridge_act.holds")
    if [ "$which" != none ]; then
        printf 'bridge_act|%s|%s|open\n' "$name" "$(address "$1.$which")"
    fi
}

# actual_customer ID VALID_TO: the same lines, from the warehouse, and the row counts.
actual_customer() {
    duckdb "SELECT 'history', name, street, postal_code, city, country,
                   CASE WHEN $2 IS NULL THEN 'open' ELSE 'closed' END
              FROM ${prefix}_hst.$reveal_object WHERE $key = '$1'
            UNION ALL
            SELECT 'bridge_act', name, street, postal_code, city, country, 'open'
              FROM ${prefix}_bdg.${reveal_object}_act WHERE $key = '$1'"
}

# assert_reveal: after run 2, the history and bridge_act rows of both customers.
assert_reveal() {
    # The mover's blocks sit at reveal.*, the deleted customer's at reveal.deleted.*.
    for who in mover deleted; do
        case $who in
            mover) block=reveal id=$mover ;;
            deleted) block=reveal.deleted id=$deleted ;;
        esac
        name=$(get "$block.name")
        valid_to=$(identifier "$(get "$block.history.valid_to_column")")
        expect_customer "$block" >"$tmp/$who.unsorted"
        sort "$tmp/$who.unsorted" >"$tmp/$who.expected"
        actual_customer "$id" "$valid_to" >"$tmp/$who.unsorted"
        sort "$tmp/$who.unsorted" >"$tmp/$who.actual"
        history_rows=$(grep -c '^history|' "$tmp/$who.actual" || true)
        bridge_rows=$(grep -c '^bridge_act|' "$tmp/$who.actual" || true)
        [ "$history_rows" = "$(get "$block.history.rows")" ] \
            || die "$name has $history_rows rows in ${prefix}_hst.$reveal_object, not $(get "$block.history.rows")"
        [ "$bridge_rows" = "$(get "$block.bridge_act.rows")" ] \
            || die "$name has $bridge_rows rows in ${prefix}_bdg.${reveal_object}_act, not $(get "$block.bridge_act.rows")"
        diff -u "$tmp/$who.expected" "$tmp/$who.actual" \
            || die "the warehouse rows of $name differ from the reveal in $counts_name"
        sed 's/^/  /' "$tmp/$who.actual"
        echo "ok: $name: $history_rows history rows and $bridge_rows bridge_act rows, as the reveal says ($valid_to)"
    done
}

# project_resources: the ids of every container, volume and network of the project.
project_resources() {
    docker ps -aq --filter "label=com.docker.compose.project=$project"
    docker volume ls -q --filter "label=com.docker.compose.project=$project"
    docker network ls -q --filter "label=com.docker.compose.project=$project"
}

# The reader connection of Review Focus 4: a DuckDB shell kept open on a pipe.
reader_pid=""
open_reader() {
    mkfifo "$tmp/reader.in"
    (cd "$jaffle" && exec docker compose run --rm -T duckdb) <"$tmp/reader.in" >"$tmp/reader.out" 2>&1 &
    reader_pid=$!
    exec 3>"$tmp/reader.in"
    # The shell answers this once it has opened the file, and so holds its lock.
    printf "SELECT 'reader is open';\n" >&3
    i=0
    until grep -q 'reader is open' "$tmp/reader.out" 2>/dev/null; do
        kill -0 "$reader_pid" 2>/dev/null || { cat "$tmp/reader.out" >&2; die "the DuckDB shell exited"; }
        i=$((i + 1))
        [ "$i" -le 120 ] || die "the DuckDB shell did not answer within 60 seconds"
        sleep 0.5
    done
}

close_reader() {
    if [ -n "$reader_pid" ]; then
        exec 3>&-
        wait "$reader_pid" || true
        reader_pid=""
    fi
}

started=0
created_env=0
tmp=$(mktemp -d)
cleanup() {
    status=$?
    trap - EXIT
    close_reader
    if [ "$started" -eq 1 ]; then
        say "cleaning up after a failure: steps/99-reset.sh"
        "$jaffle/steps/99-reset.sh" || true
    fi
    if [ "$created_env" -eq 1 ]; then
        rm -f "$jaffle/.env"
    fi
    rm -rf "$tmp"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# --- Before anything runs ------------------------------------------------------------------

say "checking the image pin, the counts file and the project file"
sh "$here/check-image-pin.sh" "$jaffle"
python3 "$jaffle/expected/counts.py" validate "$counts"
python3 "$jaffle/check_project.py" "$jaffle/project/amfelt.yaml" "$counts" "$jaffle/project/IMAGE"

prefix=$(identifier "$(get job.schemaPrefix)")
reveal_object=$(identifier "$(get reveal.object)")
key=$(identifier "$(get reveal.key_column)")
mover=$(get reveal.customer_id)
deleted=$(get reveal.deleted.customer_id)
# The deleted customer is the one run 2 deletes: db/run2.sql must say the same.
from_run2=$(sed -n "s/^DELETE FROM sales\.$reveal_object WHERE $key = '\([0-9a-f-]*\)';\$/\1/p" "$jaffle/db/run2.sql")
[ "$from_run2" = "$deleted" ] \
    || die "db/run2.sql deletes the $reveal_object '$from_run2', the counts file names '$deleted'"
echo "ok: db/run2.sql deletes $deleted, the deleted customer of the reveal"

say "checking that nothing of the compose project $project exists yet"
leftover=$(project_resources)
[ -z "$leftover" ] || die "the compose project $project already has containers, volumes or networks; they are not this run's, so it stops"
for dir in work warehouse; do
    [ ! -e "$jaffle/$dir" ] || die "$jaffle/$dir exists; run steps/99-reset.sh first"
done
if [ -e "$jaffle/.env" ]; then
    cmp -s "$jaffle/.env" "$jaffle/.env.example" \
        || die "$jaffle/.env differs from .env.example, so the run would not use the pinned image"
else
    say "cp .env.example .env"
    (cd "$jaffle" && cp .env.example .env)
    created_env=1
fi
echo "ok: nothing of $project exists"

# --- Steps 1 to 4: run 1 ---------------------------------------------------------------------

started=1
run_step 01-start.sh
assert_source run1
run_step 02-discover.sh
run_step 03-configure.sh
run_step 04-run1.sh

say "asserting run 1's counts"
snapshot >"$tmp/run1.counts"
assert_counts run1 "$tmp/run1.counts"

# --- Review Focus 3: the reader runs run 1's sync again ----------------------------------------

say "Review Focus 3: run 1's sync again, on the unchanged source"
with_lib "$sync_cmd"
snapshot >"$tmp/repeat.counts"
diff -u "$tmp/run1.counts" "$tmp/repeat.counts" \
    || die "a second sync on the unchanged source changed the counts"
echo "ok: the second sync left all 28 counts unchanged"

# --- Review Focus 4: a DuckDB shell holds the file open -----------------------------------------

say "Review Focus 4: run 1's sync while a DuckDB shell holds the warehouse open"
open_reader
if with_lib "$sync_cmd" >"$tmp/lock.out" 2>"$tmp/lock.err"; then
    die "the sync succeeded while a DuckDB shell held the warehouse open"
else
    lock_status=$?
fi
close_reader
echo "the sync exited $lock_status; its error output:"
sed 's/^/  /' "$tmp/lock.err"
grep -q 'Could not set lock on file' "$tmp/lock.err" \
    || die "the sync failed, but its error output does not name the lock"
echo "ok: the sync failed with exit $lock_status, and its error output names the lock"
snapshot >"$tmp/locked.counts"
diff -u "$tmp/run1.counts" "$tmp/locked.counts" \
    || die "the failed sync changed the warehouse"
echo "ok: the failed sync left all 28 counts unchanged"

# --- Steps 5 and 6: run 2 and the reveal -------------------------------------------------------

run_step 05-change-source.sh
assert_source run2
run_step 06-run2-reveal.sh

say "asserting run 2's counts"
snapshot >"$tmp/run2.counts"
assert_counts run2 "$tmp/run2.counts"

say "asserting the history reveal"
assert_reveal

# --- Review Focus 3: the documented reset returns to step 1 ------------------------------------

run_step 99-reset.sh
[ -z "$(project_resources)" ] || die "steps/99-reset.sh left containers, volumes or networks of $project"
run_step 01-start.sh
say "asserting that step 1 after the reset starts over"
for dir in work warehouse; do
    [ -d "$jaffle/$dir" ] || die "step 1 did not make $dir/"
done
# Step 1 makes the folders, work/warehouse/ among them; nothing else may be in them.
leftover=$(find "$jaffle/work" "$jaffle/warehouse" ! -type d | head -n 5)
[ -z "$leftover" ] || die "work/ or warehouse/ still holds files after the reset: $leftover"
assert_source run1
echo "ok: after the reset, step 1 holds the freshly loaded source and empty work/ and warehouse/"

# --- The end -----------------------------------------------------------------------------------

run_step 99-reset.sh
started=0
[ -z "$(project_resources)" ] || die "steps/99-reset.sh left containers, volumes or networks of $project"
say "PASS: the tutorial's steps ran unchanged, and every assertion holds"

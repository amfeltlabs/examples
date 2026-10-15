#!/bin/sh
# shellcheck disable=SC2030,SC2031 # with_counts sets counts in a subshell on purpose
# Run the Jaffle tutorial as a reader runs it, and assert what it publishes.
#
# Usage: sh jaffle/steps/ci/tutorial.sh [JAFFLE_DIR]
#
# The steps in JAFFLE_DIR/steps run unchanged, in the reader's order, with the amfelt CLI
# on the PATH, as a reader installs it. This script adds no step of its own to the
# tutorial; around the steps it asserts, from its own queries:
#
#   - before step 1, that the amfelt on the PATH reports project/IMAGE's AGENT_VERSION;
#   - after step 2, that the agent's workspace work/.amfelt holds exactly one
#     source-postgres and one target-duckdb version, SOURCE_POSTGRES_VERSION and
#     TARGET_DUCKDB_VERSION, the ones the steps' project files declare and install;
#   - before step 1, that the internal image project/IMAGE pins, which the stack's
#     capture runs, is what project/IMAGE says it is: its `amfelt version` is
#     AGENT_VERSION, and it bakes exactly one source-postgres and one target-duckdb
#     version, SOURCE_POSTGRES_VERSION and TARGET_DUCKDB_VERSION. Docker ignores the tag
#     where a digest is present, so nothing else binds those labels. The 4.x images of
#     earlier release tags have `infusal-agent version` in its place;
#   - after steps 4 and 6, every warehouse count of that run in expected/counts.yaml, and
#     after step 6 the history reveal: the rows of the mover and of the deleted customer
#     in the history table and in the bridge view of open versions;
#   - step 3 with the source published on another port than .env's: it passes, and the
#     contract it wrote records that port, so step 3's mapping of the port onto the
#     ready-made contract's was exercised; the source is then published on .env's port
#     again and step 3 run once more before step 4;
#   - a reader re-running step 4, run 1's sync included: the counts stay the same (plan
#     Review Focus 3);
#   - a reader leaving a DuckDB shell open: the sync fails, and its error output names the
#     lock (Review Focus 4); the warehouse is unchanged afterwards. On Linux only, and so
#     always in CI: Docker Desktop on macOS does not pass the container's lock to the agent;
#   - the documented reset, steps/99-reset.sh (docker compose down --volumes): step 1 then
#     starts from the freshly loaded source, with no project and no warehouse (Focus 3).
#
# It ends with steps/99-reset.sh, and runs it on any failure as well.
#
# Negative controls prove the assertions can fail. The image check, and after step 2 the
# check of the installed CLI and plugins, must each reject a project/IMAGE whose
# AGENT_VERSION, SOURCE_POSTGRES_VERSION or TARGET_DUCKDB_VERSION they do not match, and canned
# probe output with each near miss: a version that is a prefix of the wanted one or has it
# as its prefix, two baked plugin versions with the wanted one first or last, none, two
# agent version lines, or the version line of another binary or of none. After run 2, the
# count and reveal assertions are run again on the rows they just passed, against a copy of
# the counts file with one count, and one city of the reveal, changed; each must fail, for
# that reason. Without a second engine run.
#
# JAFFLE_DIR is the jaffle/ directory whose steps, data and expected counts are tested; it
# defaults to the one this script sits in. CI passes the tree of the ref it tests, so this
# script can test the steps of an earlier commit, such as a release tag.
#
# TUTORIAL_COUNTS names the counts file the assertions read, by default
# JAFFLE_DIR/expected/counts.yaml. A copy with one count changed must turn the run red;
# the steps keep reading their own expected/counts.yaml.
#
# The steps install the plugins with gh from private GitHub releases until the release
# channel exists (steps/install-from-github.sh), so gh must be signed in, or GH_TOKEN set,
# with read access to amfeltlabs/hub.
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

# Run 1's sync, as steps/04-run1.sh spells it: the preflight checks that both lines still
# appear there verbatim, so Review Focus 4 tests the command a reader runs.
# shellcheck disable=SC2016 # expanded by with_lib's shell
sync_url_line='url=$(source_url)'
# shellcheck disable=SC2016 # expanded by with_lib's shell
sync_line='(cd work && amfelt sync --job load --source-url "$url" --level warn)'
sync_cmd="$sync_url_line; $sync_line"

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

# reveal_block WHO: the counts file's block of the mover or the deleted customer, and the
# id: the mover's blocks sit at reveal.*, the deleted customer's at reveal.deleted.*.
reveal_block() {
    case $1 in
        mover) block=reveal id=$mover ;;
        deleted) block=reveal.deleted id=$deleted ;;
    esac
}

# capture_reveal: after run 2, save the warehouse rows of both customers of the reveal in
# $tmp/mover.actual and $tmp/deleted.actual, for check_reveal.
capture_reveal() {
    for who in mover deleted; do
        reveal_block "$who"
        valid_to=$(identifier "$(get "$block.history.valid_to_column")")
        actual_customer "$id" "$valid_to" >"$tmp/$who.unsorted"
        sort "$tmp/$who.unsorted" >"$tmp/$who.actual"
    done
}

# check_reveal [DIR]: the saved rows of both customers, DIR/mover.actual and
# DIR/deleted.actual ($tmp by default), hold what the counts file's reveal says.
check_reveal() {
    rows=${1:-$tmp}
    for who in mover deleted; do
        reveal_block "$who"
        name=$(get "$block.name")
        expect_customer "$block" >"$rows/$who.unsorted"
        sort "$rows/$who.unsorted" >"$rows/$who.expected"
        history_rows=$(grep -c '^history|' "$rows/$who.actual" || true)
        bridge_rows=$(grep -c '^bridge_act|' "$rows/$who.actual" || true)
        [ "$history_rows" = "$(get "$block.history.rows")" ] \
            || die "$name has $history_rows rows in ${prefix}_hst.$reveal_object, not $(get "$block.history.rows")"
        [ "$bridge_rows" = "$(get "$block.bridge_act.rows")" ] \
            || die "$name has $bridge_rows rows in ${prefix}_bdg.${reveal_object}_act, not $(get "$block.bridge_act.rows")"
        diff -u "$rows/$who.expected" "$rows/$who.actual" \
            || die "the warehouse rows of $name differ from the reveal in $counts_name"
        sed 's/^/  /' "$rows/$who.actual"
        echo "ok: $name: $history_rows history rows and $bridge_rows bridge_act rows, as the reveal says"
    done
}

# with_counts FILE FUNCTION ARGS...: run FUNCTION in a subshell that reads FILE as the
# counts file, so a failing assertion ends the subshell and not this script.
with_counts() {
    (
        counts=$1
        counts_name=$1
        shift
        "$@"
    )
}

# must_fail WHAT TEXT COMMAND...: a negative control. COMMAND must fail, and its output must
# hold TEXT, so it fails for the reason the control is about and not for another.
must_fail() {
    what=$1
    text=$2
    shift 2
    if "$@" >"$tmp/control.out" 2>&1; then
        sed 's/^/  | /' "$tmp/control.out"
        die "negative control: $what passed; the assertion it tests cannot fail"
    fi
    if ! grep -qF -- "$text" "$tmp/control.out"; then
        sed 's/^/  | /' "$tmp/control.out"
        die "negative control: $what failed, but not with: $text"
    fi
    grep -F -- "$text" "$tmp/control.out" | sed 's/^/  | /'
    echo "ok: negative control: $what fails, as it must"
}

# perturb NAME SED-SCRIPT FILE: a copy of FILE in $tmp/NAME with SED-SCRIPT applied, which
# must change it; prints the copy's path.
perturb() {
    mkdir -p "$tmp/$1"
    out=$tmp/$1/$(basename "$3")
    sed "$2" "$3" >"$out"
    if cmp -s "$3" "$out"; then
        die "negative control $1: the change to $(basename "$3") changed nothing"
    fi
    printf '%s\n' "$out"
}

# image_value FILE KEY: the value of the one KEY= line in the IMAGE file FILE.
image_value() {
    n=$(grep -c "^$2=" "$1" || true)
    [ "$n" -eq 1 ] || { echo "error: $1 sets $2 on $n lines, not exactly one" >&2; return 1; }
    sed -n "s/^$2=//p" "$1"
}

# check_probe FILE REF AGENT PG DUCK: check the probe output FILE of REF, the image or the
# host install, as check_image_contents and check_install_contents write it: the agent
# binary found (bin=), that binary's version line (agent=), and one pg= and one duck= line
# per installed plugin version, or the glob itself where none is. The version line must start with the binary's own name, then
# AGENT; the plugins must be PG only and DUCK only. Prints ok: lines, or error: lines and
# fails. An error quotes the agent= lines space-joined, so it stays one line.
check_probe() {
    probe=$1 probe_ref=$2 want_agent=$3 want_pg=$4 want_duck=$5
    bin=$(sed -n 's/^bin=//p' "$probe")
    agent_lines=$(grep -c '^agent=' "$probe" || true)
    agent_line=$(sed -n 's/^agent=//p' "$probe" | tr '\n' ' ')
    agent_line=${agent_line% }
    got_name=${agent_line%% *}
    got_agent=${agent_line#"$got_name"}
    got_agent=${got_agent# }
    got_agent=${got_agent%% *}
    got_pg=$(sed -n 's/^pg=//p' "$probe" | tr '\n' ' ')
    got_duck=$(sed -n 's/^duck=//p' "$probe" | tr '\n' ' ')
    bad=0
    case $bin in
        amfelt | infusal-agent)
            if [ "$agent_lines" -ne 1 ] || [ "$got_name" != "$bin" ]; then
                echo "error: $probe_ref's \`$bin version\` prints '$agent_line', not one line that starts with $bin" >&2
                bad=1
            elif [ "$got_agent" = "$want_agent" ]; then
                echo "ok: $probe_ref reports $bin version $got_agent, AGENT_VERSION"
            else
                echo "error: $probe_ref reports $bin version '$got_agent', project/IMAGE's AGENT_VERSION is $want_agent" >&2
                bad=1
            fi
            ;;
        *)
            echo "error: $probe_ref has neither amfelt nor infusal-agent on its PATH" >&2
            bad=1
            ;;
    esac
    if [ "$got_pg" = "$want_pg " ]; then
        echo "ok: $probe_ref holds source-postgres $want_pg only, SOURCE_POSTGRES_VERSION"
    else
        echo "error: $probe_ref holds source-postgres '${got_pg% }', project/IMAGE's SOURCE_POSTGRES_VERSION is $want_pg" >&2
        bad=1
    fi
    if [ "$got_duck" = "$want_duck " ]; then
        echo "ok: $probe_ref holds target-duckdb $want_duck only, TARGET_DUCKDB_VERSION"
    else
        echo "error: $probe_ref holds target-duckdb '${got_duck% }', project/IMAGE's TARGET_DUCKDB_VERSION is $want_duck" >&2
        bad=1
    fi
    return "$bad"
}

# check_image_contents IMAGE-FILE: run the image IMAGE-FILE pins, probe its agent binary
# (amfelt, or infusal-agent in the 4.x images of earlier release tags) and the plugin
# versions under /opt/amfelt/plugins, and check them with check_probe against
# AGENT_VERSION, v$SOURCE_POSTGRES_VERSION and v$TARGET_DUCKDB_VERSION.
check_image_contents() {
    f=$1
    image=$(image_value "$f" AGENT_IMAGE) || return 1
    image_tag=$(image_value "$f" AGENT_IMAGE_TAG) || return 1
    image_digest=$(image_value "$f" AGENT_IMAGE_DIGEST) || return 1
    ref=$image:$image_tag@$image_digest
    want_agent=$(image_value "$f" AGENT_VERSION) || return 1
    want_pg=v$(image_value "$f" SOURCE_POSTGRES_VERSION) || return 1
    want_duck=v$(image_value "$f" TARGET_DUCKDB_VERSION) || return 1
    docker run --rm --entrypoint sh "$ref" -c '
        for b in amfelt infusal-agent; do
            if command -v "$b" >/dev/null 2>&1; then
                echo "bin=$b"
                echo "agent=$("$b" version)"
                break
            fi
        done
        for v in /opt/amfelt/plugins/source/amfelt/postgres/*; do echo "pg=${v##*/}"; done
        for v in /opt/amfelt/plugins/target/amfelt/duckdb/*; do echo "duck=${v##*/}"; done
    ' >"$tmp/image.out" || { echo "error: cannot run $ref" >&2; return 1; }
    check_probe "$tmp/image.out" "$ref" "$want_agent" "$want_pg" "$want_duck"
}

# check_install_contents IMAGE-FILE: probe what a reader's steps run with: the amfelt on
# the PATH and the plugin versions steps 2 and 3 installed in the agent's workspace,
# JAFFLE_DIR/work/.amfelt, where the agent run in work/ looks for them. Check them with
# check_probe against the versions IMAGE-FILE names, as check_image_contents checks the image.
check_install_contents() {
    f=$1
    want_agent=$(image_value "$f" AGENT_VERSION) || return 1
    want_pg=v$(image_value "$f" SOURCE_POSTGRES_VERSION) || return 1
    want_duck=v$(image_value "$f" TARGET_DUCKDB_VERSION) || return 1
    plugins=$jaffle/work/.amfelt/plugins
    {
        if command -v amfelt >/dev/null 2>&1; then
            echo "bin=amfelt"
            echo "agent=$(NO_COLOR=1 amfelt version)"
        fi
        for v in "$plugins"/source/amfelt/postgres/*; do echo "pg=${v##*/}"; done
        for v in "$plugins"/target/amfelt/duckdb/*; do echo "duck=${v##*/}"; done
    } >"$tmp/install.out"
    check_probe "$tmp/install.out" "the host install" "$want_agent" "$want_pg" "$want_duck"
}

# install_control NAME WHAT SED-SCRIPT TEXT: check_install_contents must reject
# project/IMAGE changed by SED-SCRIPT, with one error, holding TEXT. WHAT says what changed.
install_control() {
    image_copy=$(perturb "install-$1" "$3" "$jaffle/project/IMAGE")
    must_fail "the install check with $2 in project/IMAGE" "$4" check_install_contents "$image_copy"
    one_error "the install check with $2 in project/IMAGE"
}

# one_error WHAT: the control's output, $tmp/control.out, holds exactly one error: line,
# so the control WHAT failed for its own reason only.
one_error() {
    errors=$(grep -c '^error:' "$tmp/control.out" || true)
    [ "$errors" -eq 1 ] || die "negative control: $1 failed with $errors error lines, not one"
}

# image_control NAME WHAT SED-SCRIPT TEXT: check_image_contents must reject project/IMAGE
# changed by SED-SCRIPT, with one error, holding TEXT. WHAT says what changed.
image_control() {
    image_copy=$(perturb "image-$1" "$3" "$jaffle/project/IMAGE")
    must_fail "the image check with $2 in project/IMAGE" "$4" check_image_contents "$image_copy"
    one_error "the image check with $2 in project/IMAGE"
}

# The canned probe output of a 5.x image that check_probe accepts for agent 5.0.7,
# source-postgres v4.9.1 and target-duckdb v2.12.1; each probe control changes one part.
probe_bin='bin=amfelt'
probe_agent='agent=amfelt 5.0.7 linux/arm64 BuildDate=2026-10-14T11:45:36Z Commit=7e33ba8'
probe_pg='pg=v4.9.1'
probe_duck='duck=v2.12.1'

# probe_control WHAT TEXT AGENT PG DUCK LINE...: check_probe must reject the canned probe
# output LINE... against AGENT, PG and DUCK, with one error, holding TEXT.
probe_control() {
    pc_what="the image check on canned probe output with $1"
    pc_text=$2 pc_agent=$3 pc_pg=$4 pc_duck=$5
    shift 5
    printf '%s\n' "$@" >"$tmp/probe.out"
    must_fail "$pc_what" "$pc_text" check_probe "$tmp/probe.out" canned "$pc_agent" "$pc_pg" "$pc_duck"
    one_error "$pc_what"
}

# probe_controls: check_probe must accept the canned probe output of a 5.x and of a 4.x
# image, and reject each near miss of it: a version that is a prefix of the wanted one, or
# has it as its prefix; two baked plugin versions, the wanted one first or last, so that a
# comparison of the first or of the last alone fails either way; no baked version, where
# the glob is echoed; two agent version lines; and a version line of another binary, or
# none.
probe_controls() {
    printf '%s\n' "$probe_bin" "$probe_agent" "$probe_pg" "$probe_duck" >"$tmp/probe.out"
    check_probe "$tmp/probe.out" canned 5.0.7 v4.9.1 v2.12.1 >/dev/null \
        || die "the image check rejects the canned probe output of a 5.x image"
    printf '%s\n' bin=infusal-agent "agent=infusal-agent 4.28.0 linux/arm64 BuildDate=x Commit=y" \
        "$probe_pg" "$probe_duck" >"$tmp/probe.out"
    check_probe "$tmp/probe.out" canned 4.28.0 v4.9.1 v2.12.1 >/dev/null \
        || die "the image check rejects the canned probe output of a 4.x image"
    echo "ok: the image check accepts the canned probe output of a 5.x and of a 4.x image"
    probe_control "agent 5.0.70 for 5.0.7" \
        "reports amfelt version '5.0.70', project/IMAGE's AGENT_VERSION is 5.0.7" 5.0.7 v4.9.1 v2.12.1 \
        "$probe_bin" "agent=amfelt 5.0.70 linux/arm64" "$probe_pg" "$probe_duck"
    probe_control "agent 5.0.7 for 5.0.70" \
        "reports amfelt version '5.0.7', project/IMAGE's AGENT_VERSION is 5.0.70" 5.0.70 v4.9.1 v2.12.1 \
        "$probe_bin" "$probe_agent" "$probe_pg" "$probe_duck"
    probe_control "source-postgres v4.9.10 for v4.9.1" \
        "source-postgres 'v4.9.10', project/IMAGE's SOURCE_POSTGRES_VERSION is v4.9.1" 5.0.7 v4.9.1 v2.12.1 \
        "$probe_bin" "$probe_agent" pg=v4.9.10 "$probe_duck"
    probe_control "source-postgres v4.9.1 for v4.9.10" \
        "source-postgres 'v4.9.1', project/IMAGE's SOURCE_POSTGRES_VERSION is v4.9.10" 5.0.7 v4.9.10 v2.12.1 \
        "$probe_bin" "$probe_agent" "$probe_pg" "$probe_duck"
    probe_control "target-duckdb v2.12.10 for v2.12.1" \
        "target-duckdb 'v2.12.10', project/IMAGE's TARGET_DUCKDB_VERSION is v2.12.1" 5.0.7 v4.9.1 v2.12.1 \
        "$probe_bin" "$probe_agent" "$probe_pg" duck=v2.12.10
    probe_control "target-duckdb v2.12.1 for v2.12.10" \
        "target-duckdb 'v2.12.1', project/IMAGE's TARGET_DUCKDB_VERSION is v2.12.10" 5.0.7 v4.9.1 v2.12.10 \
        "$probe_bin" "$probe_agent" "$probe_pg" "$probe_duck"
    probe_control "two source-postgres versions" \
        "source-postgres 'v4.9.0 v4.9.1', project/IMAGE's SOURCE_POSTGRES_VERSION is v4.9.1" 5.0.7 v4.9.1 v2.12.1 \
        "$probe_bin" "$probe_agent" pg=v4.9.0 "$probe_pg" "$probe_duck"
    probe_control "two source-postgres versions, the wanted one first" \
        "source-postgres 'v4.9.1 v4.9.2', project/IMAGE's SOURCE_POSTGRES_VERSION is v4.9.1" 5.0.7 v4.9.1 v2.12.1 \
        "$probe_bin" "$probe_agent" "$probe_pg" pg=v4.9.2 "$probe_duck"
    probe_control "two target-duckdb versions" \
        "target-duckdb 'v2.12.1 v2.12.2', project/IMAGE's TARGET_DUCKDB_VERSION is v2.12.1" 5.0.7 v4.9.1 v2.12.1 \
        "$probe_bin" "$probe_agent" "$probe_pg" "$probe_duck" duck=v2.12.2
    probe_control "two target-duckdb versions, the wanted one last" \
        "target-duckdb 'v2.12.0 v2.12.1', project/IMAGE's TARGET_DUCKDB_VERSION is v2.12.1" 5.0.7 v4.9.1 v2.12.1 \
        "$probe_bin" "$probe_agent" "$probe_pg" duck=v2.12.0 "$probe_duck"
    probe_control "no source-postgres version" \
        "source-postgres '*', project/IMAGE's SOURCE_POSTGRES_VERSION is v4.9.1" 5.0.7 v4.9.1 v2.12.1 \
        "$probe_bin" "$probe_agent" 'pg=*' "$probe_duck"
    probe_control "no target-duckdb version" \
        "target-duckdb '*', project/IMAGE's TARGET_DUCKDB_VERSION is v2.12.1" 5.0.7 v4.9.1 v2.12.1 \
        "$probe_bin" "$probe_agent" "$probe_pg" 'duck=*'
    probe_control "the version line of another binary" \
        "prints 'infusal-agent 5.0.7 linux/arm64', not one line that starts with amfelt" 5.0.7 v4.9.1 v2.12.1 \
        "$probe_bin" "agent=infusal-agent 5.0.7 linux/arm64" "$probe_pg" "$probe_duck"
    probe_control "two agent version lines" \
        "prints 'amfelt 5.0.7 linux/arm64 amfelt 5.0.7 linux/arm64', not one line that starts with amfelt" \
        5.0.7 v4.9.1 v2.12.1 \
        "$probe_bin" "agent=amfelt 5.0.7 linux/arm64" "agent=amfelt 5.0.7 linux/arm64" "$probe_pg" "$probe_duck"
    probe_control "no agent binary" \
        "has neither amfelt nor infusal-agent on its PATH" 5.0.7 v4.9.1 v2.12.1 \
        "$probe_pg" "$probe_duck"
}

# rows_control NAME WHAT WHO SED-SCRIPT: check_reveal must reject the saved rows with
# WHO's rows (mover or deleted) changed by SED-SCRIPT, as an engine defect would change
# them. The expectation is untouched.
rows_control() {
    changed=$(perturb "$1" "$4" "$tmp/$3.actual")
    for who in mover deleted; do
        [ "$who" = "$3" ] || cp "$tmp/$who.actual" "$(dirname "$changed")/$who.actual"
    done
    must_fail "the reveal with $2" "differ from the reveal" \
        with_counts "$counts" check_reveal "$(dirname "$changed")"
}

# run2_controls: the count and reveal assertions must reject run 2's saved rows against a
# counts file, or rows, changed in one place each.
run2_controls() {
    # One count: customers' history, with its bridge_hst mirror, so counts.py still
    # accepts the file and only the comparison with the warehouse can object.
    count_copy=$(perturb counts \
        '/^  run2:/,/^$/{/^    history: /s/customers: \([0-9]*\)/customers: 1\1/;/^    bridge_hst: /s/customers: \([0-9]*\)/customers: 1\1/;}' \
        "$counts")
    python3 "$jaffle/expected/counts.py" validate "$count_copy" >/dev/null
    must_fail "the run 2 counts with customers' history changed" "2 of 28 warehouse counts differ" \
        with_counts "$count_copy" assert_counts run2 "$tmp/run2.counts"
    # The mover's closed version, the address she lived at before: its city.
    city_copy=$(perturb city '/^  before: /s/city: "[^"]*"/city: "Pittsburgh"/' "$counts")
    python3 "$jaffle/expected/counts.py" validate "$city_copy" >/dev/null
    must_fail "the mover's closed-row city changed in the counts file" "differ from the reveal" \
        with_counts "$city_copy" check_reveal
    # The deleted customer's closed version: its city.
    deleted_copy=$(perturb deleted '/^    before: /s/city: "[^"]*"/city: "Pittsburgh"/' "$counts")
    python3 "$jaffle/expected/counts.py" validate "$deleted_copy" >/dev/null
    must_fail "the deleted customer's closed-row city changed in the counts file" "differ from the reveal" \
        with_counts "$deleted_copy" check_reveal
    # The mover's open versions hold the after address, whose city counts.py fixes, so
    # these change the saved rows instead: the open history row, then the bridge_act row.
    after_city=$(get reveal.after.city)
    rows_control open-city "the mover's open history row in another city" mover \
        "/^history|.*|open\$/s/|$after_city|/|Pittsburgh|/"
    rows_control bridge-city "the mover's bridge_act row in another city" mover \
        "/^bridge_act|/s/|$after_city|/|Pittsburgh|/"
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

say "checking the amfelt CLI, the counts file and the project file"
command -v amfelt >/dev/null || die "amfelt is not on the PATH; install it as README.md says"
want_agent=$(image_value "$jaffle/project/IMAGE" AGENT_VERSION) || die "project/IMAGE names no AGENT_VERSION"
case $(NO_COLOR=1 amfelt version 2>&1) in
    "amfelt $want_agent" | "amfelt $want_agent "*) echo "ok: the amfelt on the PATH reports $want_agent, project/IMAGE's AGENT_VERSION" ;;
    *) die "the amfelt on the PATH reports '$(NO_COLOR=1 amfelt version 2>&1)', not project/IMAGE's AGENT_VERSION $want_agent" ;;
esac
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

for line in "$sync_url_line" "$sync_line"; do
    grep -qxF -- "$line" "$jaffle/steps/04-run1.sh" \
        || die "steps/04-run1.sh no longer holds the line: $line; Review Focus 4 would test a command no reader runs"
done
echo "ok: the sync of Review Focus 4 is the one steps/04-run1.sh runs"

say "checking that the pinned image is the version project/IMAGE says"
check_image_contents "$jaffle/project/IMAGE" || die "the pinned image is not what project/IMAGE says it is"
say "negative controls: the image's versions"
probe_controls
image_control agent "the agent version changed" 's/^AGENT_VERSION=.*/AGENT_VERSION=0.0.0/' \
    "project/IMAGE's AGENT_VERSION is 0.0.0"
image_control postgres "the PostgreSQL plugin version changed" \
    's/^SOURCE_POSTGRES_VERSION=.*/SOURCE_POSTGRES_VERSION=0.0.0/' "project/IMAGE's SOURCE_POSTGRES_VERSION is v0.0.0"
image_control duckdb "the DuckDB plugin version changed" \
    's/^TARGET_DUCKDB_VERSION=.*/TARGET_DUCKDB_VERSION=0.0.0/' "project/IMAGE's TARGET_DUCKDB_VERSION is v0.0.0"

say "checking that nothing of the compose project $project exists yet"
leftover=$(project_resources)
[ -z "$leftover" ] || die "the compose project $project already has containers, volumes or networks; they are not this run's, so it stops"
for dir in work warehouse; do
    [ ! -e "$jaffle/$dir" ] || die "$jaffle/$dir exists; run steps/99-reset.sh first"
done
if [ -e "$jaffle/.env" ]; then
    cmp -s "$jaffle/.env" "$jaffle/.env.example" \
        || die "$jaffle/.env differs from .env.example, so the run would not use the published settings"
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

say "checking the amfelt CLI and the plugins step 2 installed"
check_install_contents "$jaffle/project/IMAGE" || die "the installed CLI and plugins are not what project/IMAGE says"
say "negative controls: the installed versions"
install_control agent "the agent version changed" 's/^AGENT_VERSION=.*/AGENT_VERSION=0.0.0/' \
    "project/IMAGE's AGENT_VERSION is 0.0.0"
install_control postgres "the PostgreSQL plugin version changed" \
    's/^SOURCE_POSTGRES_VERSION=.*/SOURCE_POSTGRES_VERSION=0.0.0/' "project/IMAGE's SOURCE_POSTGRES_VERSION is v0.0.0"
install_control duckdb "the DuckDB plugin version changed" \
    's/^TARGET_DUCKDB_VERSION=.*/TARGET_DUCKDB_VERSION=0.0.0/' "project/IMAGE's TARGET_DUCKDB_VERSION is v0.0.0"

run_step 03-configure.sh

# --- Step 3 with the source on another port ----------------------------------------------------

# SOURCE_PORT in .env is the one setting a reader changes, and the contract import writes
# records the port it reached the source on, which step 3 maps onto the ready-made
# contract's. So: publish the source on another port, run step 3 there, check that the
# contract it wrote records that port (so the mapping was exercised, not skipped), then
# publish the source on .env's port again and run step 3 once more, so step 4 starts from
# what the default run leaves.
other_port=25432
say "step 3 with the source published on $other_port instead of .env's port"
(cd "$jaffle" && SOURCE_PORT=$other_port docker compose up -d --wait source) \
    || die "cannot publish the source on $other_port"
published=$(cd "$jaffle" && docker compose port source 5432)
[ "${published##*:}" = "$other_port" ] || die "the source publishes ${published##*:}, not $other_port"
(export SOURCE_PORT=$other_port; run_step 03-configure.sh) || die "step 3 failed with the source on $other_port"
grep -q "^    port: $other_port\$" "$jaffle/work/contracts/jaffle.odcs.yaml" \
    || die "the contract step 3 wrote does not record port $other_port; the mapping was not exercised"
echo "ok: step 3 passes with the source on $other_port, and its contract records that port"
(cd "$jaffle" && docker compose up -d --wait source) || die "cannot publish the source on .env's port again"
run_step 03-configure.sh

run_step 04-run1.sh

say "asserting run 1's counts"
snapshot >"$tmp/run1.counts"
assert_counts run1 "$tmp/run1.counts"

# --- Review Focus 3: the reader runs run 1's sync again ----------------------------------------

say "Review Focus 3: the reader runs step 4 again, on the unchanged source"
run_step 04-run1.sh
snapshot >"$tmp/repeat.counts"
diff -u "$tmp/run1.counts" "$tmp/repeat.counts" \
    || die "step 4 run again on the unchanged source changed the counts"
echo "ok: step 4 run again left all 28 counts unchanged"

# --- Review Focus 4: a DuckDB shell holds the file open -----------------------------------------

# The agent runs on this machine and the DuckDB shell in a container. On Linux both share
# one kernel, which takes DuckDB's lock on the file for both; this check is what shows it
# holds. Docker Desktop on macOS runs the container in a virtual machine and does not pass
# the lock across: there the sync runs on while the shell holds the file, so this check
# cannot pass. It is skipped there, never in GitHub Actions.
if [ "$(uname -s)" != Linux ] && [ "${GITHUB_ACTIONS:-}" != true ]; then
    say "Review Focus 4: SKIPPED on $(uname -s): Docker Desktop does not pass the DuckDB shell's file lock to the agent on this machine"
else
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
fi

# --- Steps 5 and 6: run 2 and the reveal -------------------------------------------------------

run_step 05-change-source.sh
assert_source run2
run_step 06-run2-reveal.sh

say "asserting run 2's counts"
snapshot >"$tmp/run2.counts"
assert_counts run2 "$tmp/run2.counts"

say "asserting the history reveal"
capture_reveal
check_reveal

# --- Negative controls on the rows run 2 just passed --------------------------------------------

say "negative controls: the count and reveal assertions on run 2's saved rows"
run2_controls

# --- Review Focus 3: the documented reset returns to step 1 ------------------------------------

run_step 99-reset.sh
[ -z "$(project_resources)" ] || die "steps/99-reset.sh left containers, volumes or networks of $project"
run_step 01-start.sh
say "asserting that step 1 after the reset starts over"
for dir in work work/warehouse; do
    [ -d "$jaffle/$dir" ] || die "step 1 did not make $dir/"
done
[ ! -e "$jaffle/warehouse" ] || die "warehouse/ is still there after the reset"
# Step 1 makes the folders, work/warehouse/ among them; nothing else may be in them, not
# even the plugins step 2 installed in work/.amfelt.
leftover=$(find "$jaffle/work" ! -type d | head -n 5)
[ -z "$leftover" ] || die "work/ still holds files after the reset: $leftover"
assert_source run1
echo "ok: after the reset, step 1 holds the freshly loaded source and an empty work/"

# --- The end -----------------------------------------------------------------------------------

run_step 99-reset.sh
started=0
[ -z "$(project_resources)" ] || die "steps/99-reset.sh left containers, volumes or networks of $project"
say "PASS: the tutorial's steps ran unchanged, and every assertion holds"

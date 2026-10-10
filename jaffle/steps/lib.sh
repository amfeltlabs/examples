# Sourced by every step. It moves to jaffle/, checks for .env, runs the containers as you,
# and defines the helpers the steps share. It sets no shell options: each step sets its own.
# shellcheck shell=sh

cd "$(dirname "$0")/.." || exit 1

if [ ! -f .env ]; then
    echo "error: jaffle/.env is missing; create it with: cp .env.example .env" >&2
    exit 1
fi

# The agent writes work/ and warehouse/ as you, so the files it leaves there are yours.
HOST_UID=$(id -u)
HOST_GID=$(id -g)
export HOST_UID HOST_GID

# Compose prints progress lines for every short-lived container; they would bury the
# output. The slow commands of steps 1 and 99 show progress when you watch a terminal.
COMPOSE_PROGRESS=${COMPOSE_PROGRESS:-quiet}
export COMPOSE_PROGRESS
# shellcheck disable=SC2034 # steps 1 and 99 use it
if [ -t 2 ]; then
    SLOW_PROGRESS="tty"
else
    SLOW_PROGRESS="quiet"
fi

# source_url: how the agent reaches the source, as the service name on the compose network
# with the demo login. The password is the one compose gives the database, read from
# .env through compose itself and encoded for the URL. The agent warns that the URL
# carries a login; for this local demo database that is expected.
source_url() {
    password=$(docker compose config --format json | python3 -c '
import json, sys, urllib.parse
config = json.load(sys.stdin)
print(urllib.parse.quote(config["services"]["source"]["environment"]["POSTGRES_PASSWORD"], safe=""))') || return 1
    [ -n "$password" ] || return 1
    printf 'postgres://jaffle:%s@source:5432/jaffle?sslmode=disable\n' "$password"
}

# step TEXT: say what happens next, in one line.
step() {
    printf '\n==> %s\n' "$*"
}

# fail TEXT: stop the step with an error.
fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

# expected PATH: one value from expected/counts.yaml, the published expected results.
expected() {
    python3 expected/counts.py get "$1"
}

# check_counts RUN: count the rows of every object in every warehouse layer, print each
# count beside the one expected/counts.yaml publishes for RUN (run1 or run2), and fail on
# any difference. The four layers are the staging and history tables and the two bridge
# views, _act (the open versions) and _hst (every version).
check_counts() {
    run=$1
    sql=""
    n=0
    for layer in staging history bridge_act bridge_hst; do
        for object in customers stores orders order_items products supplies tweets; do
            case $layer in
                staging) relation=jaffle_stg.$object ;;
                history) relation=jaffle_hst.$object ;;
                bridge_act) relation=jaffle_bdg.${object}_act ;;
                bridge_hst) relation=jaffle_bdg.${object}_hst ;;
            esac
            n=$((n + 1))
            sql="$sql${sql:+ UNION ALL }SELECT $n, '$layer', '$object', count(*) FROM $relation"
        done
    done
    actual=$(docker compose run --rm -T duckdb -list -noheader -separator ' ' -c "$sql ORDER BY 1")

    mismatches=0
    seen=0
    printf '%-11s %-12s %9s %9s\n' layer object expected actual
    while read -r _ layer object count; do
        seen=$((seen + 1))
        want=$(expected "runs.$run.$layer.$object")
        mark=""
        if [ "$count" != "$want" ]; then
            mark="  <-- differs"
            mismatches=$((mismatches + 1))
        fi
        printf '%-11s %-12s %9s %9s%s\n' "$layer" "$object" "$want" "$count" "$mark"
    done <<EOF
$actual
EOF
    if [ "$seen" -ne "$n" ]; then
        fail "the warehouse returned $seen counts, not $n"
    fi
    if [ "$mismatches" -ne 0 ]; then
        fail "$mismatches of $n counts differ from expected/counts.yaml ($run)"
    fi
    echo "All $n counts equal expected/counts.yaml ($run)."
}

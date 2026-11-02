#!/bin/sh
# Run the reader's Compose path from a fresh clone, and assert it.
#
# Usage: sh jaffle/steps/ci/fresh-clone.sh [REPOSITORY]
#
# It clones the commit REPOSITORY has checked out (by default the repository this script
# is in), with core.autocrlf=true as a Windows reader may have it, and runs the commands
# of jaffle/README.md, with no .env and none of the settings in the environment. No agent
# and no secret is needed. It asserts that:
#
#   - work/warehouse/ exists and is writable by the reader before anything has run;
#   - .env.example holds compose.yaml's defaults: Compose resolves the same config with it
#     as with no settings file;
#   - the DuckDB shell resolves to what compose.yaml says of it: every capability dropped
#     but DAC_READ_SEARCH, a read-only root, one read-only mount, no network, no user;
#   - docker compose up -d --wait publishes the source on 127.0.0.1:15432, and the demo
#     login works over that port, from outside the container;
#   - the source holds runs.run1.source of expected/counts.yaml;
#   - db/run2.sql applies, and the source holds runs.run2.source;
#   - db/run2.sql applied again fails with its own message and hint, and changes nothing;
#   - the commands of that hint reload the source;
#   - SOURCE_PORT in .env moves the published port;
#   - the DuckDB shell reads a file only its owner can read, which this user wrote, with no
#     user id passed to it. tutorial.sh asserts the same on the file the real agent writes;
#   - git shows none of it as a change.
#
# REPOSITORY needs its full history: a ref from before this path skips all of the above and
# says so, and compose-alone.sh decides that, after negative controls on the decision.
#
# The login check reaches the source through --network host, so this script runs on Linux.
#
# Docker: the compose project is amfelt-jaffle. The script refuses to start while any
# container, volume or network of that project exists, and removes what it made.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
repository=$(cd "${1:-$here/../../..}" && pwd)
project=amfelt-jaffle
COMPOSE_PROGRESS=${COMPOSE_PROGRESS:-quiet}
export COMPOSE_PROGRESS

unset POSTGRES_PASSWORD SOURCE_PORT HOST_UID HOST_GID

say() {
    printf '\n[fresh-clone] %s\n' "$*"
}

die() {
    printf '[fresh-clone] FAIL: %s\n' "$*" >&2
    exit 1
}

# shellcheck source=compose-alone.sh
. "$here/compose-alone.sh"

project_resources() {
    docker ps -aq --filter "label=com.docker.compose.project=$project"
    docker volume ls -q --filter "label=com.docker.compose.project=$project"
    docker network ls -q --filter "label=com.docker.compose.project=$project"
}

# assert_settings: what the resolved config says, which needs no daemon and no Linux. A
# wider set of capabilities would pass the read at the end of this script as well, so the
# shell's settings are pinned, not only what they let it do.
assert_settings() {
    docker compose --profile tools config --format json >"$tmp/defaults.json"
    docker compose --profile tools --env-file .env.example config --format json >"$tmp/example.json"
    cmp -s "$tmp/defaults.json" "$tmp/example.json" \
        || die ".env.example does not hold compose.yaml's defaults"
    echo "ok: .env.example resolves to the config of no settings file"
    python3 -c '
import json, sys
shell = json.load(open(sys.argv[1]))["services"]["duckdb"]
want = {
    "cap_add": ["DAC_READ_SEARCH"],
    "cap_drop": ["ALL"],
    "read_only": True,
    "network_mode": "none",
    "user": None,
    "mounts": [("bind", "/warehouse", True)],
}
got = {key: shell.get(key) for key in want}
got["mounts"] = [(v["type"], v["target"], v.get("read_only")) for v in shell["volumes"]]
for key in want:
    if got[key] != want[key]:
        sys.exit(f"the DuckDB shell resolves {key} to {got[key]!r}, not {want[key]!r}")
' "$tmp/defaults.json" || die "the DuckDB shell is not what compose.yaml says it is"
    echo "ok: the DuckDB shell keeps DAC_READ_SEARCH only, is read-only, and has no network and no user"
}

# assert_source RUN: the source holds what expected/counts.yaml says for RUN.
assert_source() {
    python3 expected/counts.py expect "$1" >"$tmp/$1.expected"
    docker compose exec -T source psql -X -v ON_ERROR_STOP=1 -U jaffle -d jaffle \
        -v mover="$mover" -v deleted="$deleted" -f /jaffle/db/source_counts.sql >"$tmp/$1.actual"
    diff -u "$tmp/$1.expected" "$tmp/$1.actual" || die "the source differs from $2"
    echo "ok: the source holds $2"
}

# assert_port PORT: the source is published on 127.0.0.1:PORT, and the demo login works
# there from outside the container, so with the password check of a remote client.
assert_port() {
    published=$(docker compose port source 5432)
    [ "$published" = "127.0.0.1:$1" ] || die "the source publishes $published, not 127.0.0.1:$1"
    image=$(docker inspect --format '{{.Config.Image}}' "$(docker compose ps -q source)")
    login=$(docker run --rm --network host "$image" \
        psql -X -tA "postgres://jaffle:jaffle@127.0.0.1:$1/jaffle?sslmode=disable" -c 'SELECT current_user') \
        || die "the demo login failed on port $1"
    [ "$login" = jaffle ] || die "the demo login on port $1 returned '$login'"
    echo "ok: the source listens on 127.0.0.1:$1, and the demo login works there"
}

apply_run2() {
    docker compose exec -T source psql -X -U jaffle -d jaffle -f /jaffle/db/run2.sql
}

# reader_path: the whole of the run.
reader_path() {
    [ -z "$(project_resources)" ] || die "the compose project $project already exists; it is not this run's, so it stops"

    say "git clone, with core.autocrlf=true"
    git -c core.autocrlf=true clone --quiet --no-hardlinks "$repository" "$tmp/clone"
    cd "$tmp/clone/jaffle"

    [ ! -e .env ] || die "a fresh clone holds a .env"
    [ -d work/warehouse ] && [ -w work/warehouse ] || die "work/warehouse/ is not a writable folder after a fresh clone"
    echo "ok: no .env, and work/warehouse/ is there and writable"

    say "the resolved settings"
    assert_settings

    mover=$(python3 expected/counts.py get reveal.customer_id)
    deleted=$(python3 expected/counts.py get reveal.deleted.customer_id)

    say "docker compose up -d --wait, with no .env"
    started=1
    docker compose up -d --wait || die "the source did not start from a fresh clone"
    assert_port 15432
    assert_source run1 runs.run1.source

    say "applying db/run2.sql"
    apply_run2 >"$tmp/first.out" 2>&1 || { cat "$tmp/first.out" >&2; die "db/run2.sql failed on the loaded source"; }
    assert_source run2 runs.run2.source

    say "applying db/run2.sql a second time"
    if apply_run2 >"$tmp/second.out" 2>&1; then
        cat "$tmp/second.out" >&2
        die "db/run2.sql succeeded a second time"
    else
        second_status=$?
    fi
    sed 's/^/  /' "$tmp/second.out"
    grep -q 'ERROR:  db/run2.sql is already applied to this database, so it changed nothing' "$tmp/second.out" \
        || die "the second db/run2.sql failed without saying it is already applied"
    grep -q 'HINT:  .*docker compose down -v, then docker compose up -d --wait' "$tmp/second.out" \
        || die "the second db/run2.sql does not say how to reload the source"
    echo "ok: the second db/run2.sql exits $second_status and says why"
    assert_source run2 "runs.run2.source, still"

    say "the hint: docker compose down -v, then docker compose up -d --wait"
    docker compose down -v
    docker compose up -d --wait
    assert_source run1 "runs.run1.source again"

    say "SOURCE_PORT in .env"
    docker compose down
    sed 's/^SOURCE_PORT=.*/SOURCE_PORT=25432/' .env.example >.env
    docker compose up -d --wait
    assert_port 25432

    say "the DuckDB shell on a file only its owner can read"
    duckdb_image=$(docker compose --profile tools config --images | grep duckdb)
    docker run --rm --user "$(id -u):$(id -g)" --network none \
        -v "$PWD/work/warehouse:/warehouse" --entrypoint /duckdb "$duckdb_image" \
        /warehouse/jaffle.duckdb -c 'CREATE TABLE written AS SELECT 42 AS answer'
    make_private work/warehouse
    ls -ln work/warehouse
    got=$(docker compose run --rm -T duckdb -list -noheader -c 'SELECT answer FROM written') \
        || die "the DuckDB shell could not read a file only its owner can read"
    [ "$got" = 42 ] || die "the DuckDB shell returned '$got', not 42"
    echo "ok: the DuckDB shell read the file with no user id passed to it (this user is uid $(id -u))"

    say "git status after all of it"
    changed=$(git status --porcelain)
    [ -z "$changed" ] || die "the reader's clone shows changes: $changed"
    echo "ok: git ignores .env and the warehouse"

    say "PASS: the reader's Compose path holds from a fresh clone"
}

tmp=$(mktemp -d)
started=0
cleanup() {
    status=$?
    trap - EXIT
    if [ "$started" -eq 1 ]; then
        if [ "$status" -ne 0 ]; then
            (cd "$tmp/clone/jaffle" && docker compose logs --tail 20 source) || true
        fi
        (cd "$tmp/clone/jaffle" && docker compose --profile tools down --volumes) || true
    fi
    rm -rf "$tmp"
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

say "negative controls: which refs skip this run"
mkdir "$tmp/controls"
compose_alone_controls "$tmp/controls"

compose_alone_check "$repository/jaffle" "the whole of the reader's Compose path" reader_path

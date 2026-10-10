#!/bin/sh
# Step 4: run 1. Create the warehouse layers in DuckDB, load the source into them, and
# count what landed.
#
# Each source table gets a staging table (the latest copy of the source), a history table
# (every version of every row), and two bridge views over the history: _act with the open
# versions and _hst with all of them. All seven tables share one schema per layer:
# jaffle_stg, jaffle_hst and jaffle_bdg.
set -eu
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

for layer in staging history bridge; do
    step "Creating the $layer layer in warehouse/jaffle.duckdb"
    docker compose run --rm agent deploy warehouse \
        --contract contracts/jaffle.odcs.yaml --layer "$layer" --level warn
done

step "Run 1: loading every table of the source into the warehouse"
docker compose run --rm agent sync --job load --source-url "$SOURCE_URL" --level warn

step "Counting the rows in every layer, beside the published expected counts"
check_counts run1

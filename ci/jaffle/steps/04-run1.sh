#!/bin/sh
# Step 4: run 1. Create the warehouse layers in DuckDB, load the source into them, and
# count what landed.
#
# Each source table gets a staging table (the latest copy of the source), a history table,
# and two bridge views over the history: _act with the open rows and _hst with all of them.
# The history of the four SCD2 tables holds every version of every row, and closes one
# when the row changes or is deleted; the history of the three append tables holds every
# row it has seen, and closes none. All seven tables share one schema per layer:
# jaffle_stg, jaffle_hst and jaffle_bdg.
set -eu
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

for layer in staging history bridge; do
    step "Creating the $layer layer in work/warehouse/jaffle.duckdb"
    (cd work && amfelt deploy warehouse \
        --contract contracts/jaffle.odcs.yaml --layer "$layer" --level warn)
done

step "Run 1: loading every table of the source into the warehouse"
url=$(source_url)
(cd work && amfelt sync --job load --source-url "$url" --level warn)

step "Counting the rows in every layer, beside the published expected counts"
check_counts run1

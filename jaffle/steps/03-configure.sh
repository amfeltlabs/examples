#!/bin/sh
# Step 3: configure the project in work/: where the data comes from, where it goes, and
# how each table keeps its history.
#
# By default this step builds the project with the agent's commands, one at a time.
# Run it as QUICK=1 steps/03-configure.sh to copy the ready-made project from project/
# instead. Either way you end with the same project, which the last check confirms.
set -eu
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

rm -rf work/amfelt.yaml work/contracts

if [ "${QUICK:-0}" = 1 ]; then
    step "Copying the ready-made project from project/ into work/"
    cp project/amfelt.yaml work/amfelt.yaml
    cp -R project/contracts work/contracts
else
    step "Starting the project file work/amfelt.yaml"
    docker compose run --rm agent init --name jaffle

    step "Declaring the two plugins in the agent image, so it needs no download"
    cat >>work/amfelt.yaml <<'YAML'
plugins:
  source:
    - plugin: postgres
      registry: local
  target:
    - plugin: duckdb
      registry: local
YAML

    step "Adding the source: importing its three schemas into work/contracts/"
    docker compose run --rm agent import jaffle "$SOURCE_URL" \
        --namespace sales,catalog,marketing --abbreviation jaffle --level warn

    step "Adding the target: a DuckDB file in warehouse/"
    docker compose run --rm agent add-target warehouse duckdb \
        --db-path /work/warehouse/jaffle.duckdb

    step "Adding the job: every table, through staging and history to the bridge layer"
    docker compose run --rm agent add-job load --source jaffle --target warehouse \
        --include "*" --layer bridge --schema-prefix jaffle

    step "Choosing each table's type and history: SCD2 for the four that change, append for the three that only grow"
    # No command sets these yet, so they are written into the job's select block, after
    # the include list. orders and tweets also name the column that holds their event time.
    OBJECTS=$(cat <<'YAML'
      objects:
        customers:
          tableType: master
          historyMode: scd2
        products:
          tableType: reference
          historyMode: scd2
        stores:
          tableType: reference
          historyMode: scd2
        supplies:
          tableType: reference
          historyMode: scd2
        orders:
          tableType: transaction
          historyMode: append
          eventTimeCol: ordered_at
        order_items:
          tableType: transaction
          historyMode: append
        tweets:
          tableType: transaction
          historyMode: append
          eventTimeCol: tweeted_at
YAML
)
    export OBJECTS
    awk '{ print } /^        - "\*"$/ { print ENVIRON["OBJECTS"] }' work/amfelt.yaml >work/amfelt.yaml.new
    mv work/amfelt.yaml.new work/amfelt.yaml
fi

step "Checking that work/ holds the same project as project/"
# Each new project and contract gets its own id and timestamps; everything else must match.
same() {
    grep -v -E '^(id|createdAt|contractCreatedTs):|^  introspectedAt:' "$1"
}
for file in amfelt.yaml contracts/jaffle.odcs.yaml; do
    same "project/$file" >work/.expected
    same "work/$file" >work/.actual
    if ! diff -u work/.expected work/.actual; then
        rm -f work/.expected work/.actual
        fail "work/$file differs from project/$file"
    fi
done
rm -f work/.expected work/.actual
echo "work/amfelt.yaml and work/contracts/jaffle.odcs.yaml match the ready-made project."

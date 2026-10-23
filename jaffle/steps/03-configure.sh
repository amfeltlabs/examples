#!/bin/sh
# Step 3: configure the project in work/: where the data comes from, where it goes, and
# how each table keeps its history.
#
# By default this step builds the project with the agent's commands, one at a time, and
# installs the plugins it declares.
# Run it as QUICK=1 steps/03-configure.sh to copy the ready-made project from project/
# instead. Either way, the step ends by checking the project against the settings
# expected/counts.yaml was made for, and against the ready-made one.
set -eu
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

rm -rf work/amfelt.yaml work/contracts

if [ "${QUICK:-0}" = 1 ]; then
    step "Copying the ready-made project from project/ into work/"
    cp project/amfelt.yaml work/amfelt.yaml
    cp -R project/contracts work/contracts

    step "Installing the plugins the project declares"
    install_plugins amfelt.yaml
else
    step "Starting the project file work/amfelt.yaml"
    (cd work && amfelt init --name jaffle --level warn)

    step "Declaring the two plugins the project uses, each at the version the counts were made with"
    cat >>work/amfelt.yaml <<'YAML'
plugins:
  source:
    - plugin: postgres
      version: 4.10.3
      registry: local
  target:
    - plugin: duckdb
      version: 2.13.4
      registry: local
YAML

    step "Installing the plugins the project declares"
    install_plugins amfelt.yaml

    step "Adding the source: importing its three schemas into work/contracts/"
    url=$(source_url)
    (cd work && amfelt import jaffle "$url" \
        --namespace sales,catalog,marketing --abbreviation jaffle --level warn)

    step "Adding the target: a DuckDB file in work/warehouse/"
    (cd work && amfelt add-target warehouse duckdb \
        --db-path warehouse/jaffle.duckdb --level warn)

    step "Adding the job: every table, through staging and history to the bridge layer"
    (cd work && amfelt add-job load --source jaffle --target warehouse \
        --include "*" --layer bridge --schema-prefix jaffle --level warn)

    step "Choosing each table's type and history: SCD2 for the four master and reference tables, append for the three transaction tables"
    # SCD2 history closes a row's version when the row changes or is deleted, and opens a
    # new one for a change. Append history keeps every row it has seen, deleted ones
    # included, and closes nothing. add-job --history-mode sets one mode for every table
    # of the job, and advise --apply writes per-table settings that an AI model
    # recommends, which needs an API key and may differ from one call to the next. The
    # counts this tutorial publishes need these exact settings per table, so they are
    # written into the job's select block, after the include list. orders and tweets also
    # name the column that holds their event time.
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

step "Checking the project against the job expected/counts.yaml was made for"
python3 check_project.py work/amfelt.yaml expected/counts.yaml project/IMAGE

step "Checking that work/ holds the same project as project/"
# Each new project and contract gets its own id and timestamps; everything else must match.
# The contract also records the host and port import reached the source on. Here that is
# 127.0.0.1 and the port .env's SOURCE_PORT publishes; the ready-made contract records
# source and 5432, the database's name and port on the container network, where the
# stack's capture reads it. So the comparison maps this machine's values onto those.
port=$(docker compose port source 5432) || fail "the source database publishes no port; run steps/01-start.sh first"
port=${port##*:}
same() {
    grep -v -E '^(id|createdAt|contractCreatedTs):|^  introspectedAt:' "$1" \
        | sed -e 's/^    host: 127\.0\.0\.1$/    host: source/' \
              -e "s/^    port: $port\$/    port: 5432/"
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

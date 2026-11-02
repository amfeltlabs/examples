#!/bin/sh
# Step 2: discover the source. The agent reads the database's tables, columns and keys,
# and the comments on its tables and columns, and writes them down as a data contract in
# the Open Data Contract Standard (ODCS).
#
# This step works in a scratch project, work/discover/, so you can look around before you
# build the real project in step 3.
set -eu
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

step "Starting a scratch project in work/discover/"
rm -rf work/discover
(cd work && amfelt init --name discover --path discover/amfelt.yaml --level warn)

step "Declaring the two plugins the project uses, each at the version the counts were made with"
# registry: local means the agent runs the plugin itself, from its workspace work/.amfelt/.
cat >>work/discover/amfelt.yaml <<'YAML'
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
install_plugins discover/amfelt.yaml

step "Importing the three schemas of the source into one contract"
url=$(source_url)
(cd work && amfelt import jaffle "$url" \
    --namespace sales,catalog,marketing --abbreviation jaffle \
    --path discover/amfelt.yaml --level warn)

contract=work/discover/contracts/jaffle.odcs.yaml
step "What the contract in $contract says"
# A short reading of the contract: each table's description, the descriptions of one
# table's columns, and the foreign keys, which the contract records on each column.
awk '
function text(s) { sub(/^[^:]*: /, "", s); if (s ~ /^".*"$/) s = substr(s, 2, length(s) - 2); return s }
/^  - name: /            { table = text($0); next }
/^    description: /     { about[table] = text($0); next }
/^      - name: /        { column = text($0); next }
/^        description: / { if (table == "customers") columns = columns sprintf("  %-12s %s\n", column, text($0)); next }
/^        primaryKey: true/ { key[table] = key[table] (key[table] == "" ? "" : ", ") column; next }
/- property: /           { property = text($0); next }
/^ *value: / {
    if (property == "foreign_namespace") to_schema = text($0)
    else if (property == "foreign_object") to_table = text($0)
    else if (property == "foreign_property") fk[++fks] = table SUBSEP column SUBSEP to_schema SUBSEP to_table "." text($0)
    else if (property == "catalog_schema") { schema[table] = text($0); tables[++n] = table }
}
END {
    print "Tables, with their primary key and the description the source gives them:"
    for (i = 1; i <= n; i++) { t = tables[i]; printf "  %-18s %-10s %s\n", schema[t] "." t, "(" key[t] ")", about[t] }
    print "\nColumns of sales.customers, described the same way:"
    printf "%s", columns
    print "\nForeign keys; the two marked * cross from one schema into another:"
    for (i = 1; i <= fks; i++) {
        split(fk[i], f, SUBSEP)
        line = sprintf("  %-29s -> %s.%s", schema[f[1]] "." f[1] "." f[2], f[3], f[4])
        if (f[3] != schema[f[1]]) line = sprintf("%-60s *", line)
        print line
    }
}' "$contract"

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
      version: 4.19.3
      registry: local
  target:
    - plugin: duckdb
      version: 2.21.2
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
# table's columns, and the foreign keys, which the contract records as each table's
# relationships. A relationship names its columns as schema/<table id>/properties/<column
# id>, and a key of more than one column lists them, in the key's order, on both sides;
# the ids are looked up in the contract to print each column's schema, table and name.
awk '
function text(s) { sub(/^[^:]*: /, "", s); if (s ~ /^".*"$/) s = substr(s, 2, length(s) - 2); return s }
function add(s) { if (side == "from") from = from (from == "" ? "" : " ") s; else to = to (to == "" ? "" : " ") s }
function flush() { if (rel) { fk[++fks] = from SUBSEP to; rel = 0 } }
function column_of(ref,    r, i) {
    r = ref; sub(/^schema\//, "", r); i = index(r, "/properties/")
    if (i == 0 || !((substr(r, 1, i - 1), substr(r, i + 12)) in prop)) return ref
    return schema[obj[substr(r, 1, i - 1)]] "." obj[substr(r, 1, i - 1)] "." prop[substr(r, 1, i - 1), substr(r, i + 12)]
}
function columns_of(list,    n, a, i, out) {
    n = split(list, a, " "); out = ""
    for (i = 1; i <= n; i++) out = out (i > 1 ? ", " : "") column_of(a[i])
    return n > 1 ? "(" out ")" : out
}
/^  - id: /              { flush(); in_rel = 0; oid = text($0); next }
/^    name: /            { table = text($0); obj[oid] = table; next }
/^    description: /     { about[table] = text($0); next }
/^    relationships:/    { in_rel = 1; next }
/^    [a-zA-Z]/          { flush(); in_rel = 0 }
in_rel && /^      - id: / { flush(); next }
in_rel && /^        type: / { rel = 1; from = ""; to = ""; next }
in_rel && /^        (from|to): ./ { side = substr($1, 1, length($1) - 1); add($2); next }
in_rel && /^        (from|to):$/  { side = substr($1, 1, length($1) - 1); next }
in_rel && /^          - /         { add($2); next }
/^      - id: /          { pid = text($0); next }
/^        name: /        { column = text($0); prop[oid, pid] = column; next }
/^        description: / { if (table == "customers") columns = columns sprintf("  %-12s %s\n", column, text($0)); next }
/^        primaryKey: true/ { key[table] = key[table] (key[table] == "" ? "" : ", ") column; next }
/- property: /           { property = text($0); next }
/^ *value: / { if (property == "catalog_schema") { schema[table] = text($0); tables[++n] = table } }
END {
    flush()
    print "Tables, with their primary key and the description the source gives them:"
    for (i = 1; i <= n; i++) { t = tables[i]; printf "  %-27s %-10s %s\n", schema[t] "." t, "(" key[t] ")", about[t] }
    print "\nColumns of sales.customers, described the same way:"
    printf "%s", columns
    print "\nForeign keys, one per relationship; those marked * cross from one schema into another:"
    for (i = 1; i <= fks; i++) {
        split(fk[i], f, SUBSEP)
        a = columns_of(f[1]); b = columns_of(f[2])
        line = sprintf("  %-40s -> %s", a, b)
        sub(/^\(/, "", a); sub(/^\(/, "", b)
        if (substr(a, 1, index(a, ".")) != substr(b, 1, index(b, "."))) line = sprintf("%-80s *", line)
        print line
    }
}' "$contract"

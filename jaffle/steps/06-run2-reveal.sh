#!/bin/sh
# Step 6: run 2, and what the history layer remembers.
#
# The source now holds only Catherine Navarro's Brooklyn address, and no Jessica Lewis.
# The history layer keeps both of Catherine's addresses, each with the time it was valid,
# and closes Jessica's last version instead of losing it.
#
# The validity columns hold the start time of the run that opened or closed a version:
# run 1 and run 2, not the day she moved. They are shown in UTC.
set -eu
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

mover=$(expected reveal.customer_id)
deleted=$(expected reveal.deleted.customer_id)

step "Run 2: loading the changed source into the warehouse"
docker compose run --rm agent sync --job load --source-url "$SOURCE_URL" --level warn

step "Every version of Catherine Navarro and Jessica Lewis in the history layer"
docker compose run --rm -T duckdb <<SQL
SET TimeZone = 'UTC';
SELECT name, street, city,
       date_trunc('second', ifl_valid_from) AS valid_from,
       date_trunc('second', ifl_valid_to) AS valid_to
  FROM jaffle_hst.customers
 WHERE id IN ('$mover', '$deleted')
 ORDER BY name, valid_from;
SQL

step "The same two customers in the bridge view of open versions, customers_act"
docker compose run --rm -T duckdb <<SQL
SELECT name, street, city
  FROM jaffle_bdg.customers_act
 WHERE id IN ('$mover', '$deleted');
SQL

step "Checking the two customers against the reveal in expected/counts.yaml"
# One line per version: the layer, the name, the address, and whether it is still open.
address() {
    printf '%s|%s|%s|%s' "$(expected "$1.street")" "$(expected "$1.postal_code")" \
        "$(expected "$1.city")" "$(expected "$1.country")"
}
mover_name=$(expected reveal.name)
deleted_name=$(expected reveal.deleted.name)
want=$(sort <<EOF
history|$mover_name|$(address reveal.before)|closed
history|$mover_name|$(address reveal.after)|open
history|$deleted_name|$(address reveal.deleted.before)|closed
bridge_act|$mover_name|$(address reveal.after)|open
EOF
)
got=$(docker compose run --rm -T duckdb -list -noheader <<SQL | sort
SELECT 'history', name, street, postal_code, city, country,
       CASE WHEN ifl_valid_to IS NULL THEN 'open' ELSE 'closed' END
  FROM jaffle_hst.customers WHERE id IN ('$mover', '$deleted')
UNION ALL
SELECT 'bridge_act', name, street, postal_code, city, country, 'open'
  FROM jaffle_bdg.customers_act WHERE id IN ('$mover', '$deleted');
SQL
)
if [ "$got" != "$want" ]; then
    printf 'expected:\n%s\nfound:\n%s\n' "$want" "$got" >&2
    fail "the history of the two customers differs from expected/counts.yaml"
fi
echo "Catherine Navarro has a closed Philadelphia version and an open Brooklyn one;"
echo "Jessica Lewis has one closed version and is gone from customers_act."

step "Counting the rows in every layer, beside the published expected counts"
check_counts run2

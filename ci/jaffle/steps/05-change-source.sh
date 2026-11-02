#!/bin/sh
# Step 5: change the source, as a day of business would. db/run2.sql adds, changes and
# deletes rows in one transaction. Among them, Catherine Navarro moves from Philadelphia
# to Brooklyn, and Jessica Lewis is deleted.
set -eu
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

mover=$(expected reveal.customer_id)
brooklyn=$(expected reveal.after.city)

where_she_lives() {
    docker compose exec -T source psql -X -v ON_ERROR_STOP=1 -U jaffle -d jaffle -c "
        SELECT name, street, postal_code, city FROM sales.customers WHERE id = '$mover'"
}

city=$(docker compose exec -T source psql -X -v ON_ERROR_STOP=1 -U jaffle -d jaffle -tA \
    -c "SELECT city FROM sales.customers WHERE id = '$mover'")
if [ "$city" = "$brooklyn" ]; then
    step "The source already holds the changes of db/run2.sql; nothing to do"
    where_she_lives
    exit 0
fi

step "Before the change, in the source"
where_she_lives

step "Applying db/run2.sql to the source"
docker compose exec -T source psql -X -v ON_ERROR_STOP=1 -U jaffle -d jaffle \
    -f /jaffle/db/run2.sql

step "After the change, in the source"
where_she_lives

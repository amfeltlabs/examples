#!/bin/sh
# Step 5: change the source, as a day of business would. db/run2.sql adds, changes and
# deletes rows in one transaction. Among them, Nikos Lefebvre moves from Utrecht to
# Rotterdam, and Anouk Kowalski is deleted.
set -eu
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

mover=$(expected reveal.customer_id)
new_city=$(expected reveal.after.city)

where_he_lives() {
    docker compose exec -T source psql -X -v ON_ERROR_STOP=1 -U jaffle -d jaffle -c "
        SELECT name, street, postal_code, city FROM sales.customers WHERE id = '$mover'"
}

city=$(docker compose exec -T source psql -X -v ON_ERROR_STOP=1 -U jaffle -d jaffle -tA \
    -c "SELECT city FROM sales.customers WHERE id = '$mover'")
if [ "$city" = "$new_city" ]; then
    step "The source already holds the changes of db/run2.sql; nothing to do"
    where_he_lives
    exit 0
fi

step "Before the change, in the source"
where_he_lives

step "Applying db/run2.sql to the source"
docker compose exec -T source psql -X -v ON_ERROR_STOP=1 -U jaffle -d jaffle \
    -f /jaffle/db/run2.sql

step "After the change, in the source"
where_he_lives

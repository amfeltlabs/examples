#!/bin/sh
# Step 1: start the source database, and make the folders the agent writes to.
#
# The source is PostgreSQL holding the Jaffle Shop in three schemas: sales, catalog and
# marketing. Its first start loads the data; a later start finds it loaded. work/ will hold
# your project and warehouse/ the DuckDB file the agent builds.
set -eu
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

step "Checking for docker, Docker Compose 2.22 or later, and python3"
command -v docker >/dev/null || fail "docker is not installed"
version=$(docker compose version --short 2>/dev/null) || fail "Docker Compose is not installed"
major=$(echo "${version#v}" | cut -d. -f1)
minor=$(echo "${version#v}" | cut -d. -f2)
if [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -lt 22 ]; }; then
    fail "Docker Compose $version is too old; the steps need 2.22 or later"
fi
python3 expected/counts.py validate >/dev/null 2>&1 \
    || fail "python3 does not run here; the steps need it to read expected/counts.yaml"

step "Making db/ and data/ readable by the database container"
chmod -R a+rX db data

step "Pulling the source, agent and DuckDB images that are not here yet"
docker compose --progress "$SLOW_PROGRESS" --profile tools pull --policy missing

step "Making work/ for your project and warehouse/ for the DuckDB file"
mkdir -p work/warehouse warehouse

step "Starting the source database and waiting until the Jaffle Shop is loaded"
if ! docker compose --progress "$SLOW_PROGRESS" up -d --wait source; then
    docker compose logs --tail 20 source
    fail "the source database did not start; run steps/99-reset.sh, then this step again"
fi

step "The source is up. Its seven tables and their rows:"
docker compose exec -T source psql -X -v ON_ERROR_STOP=1 -U jaffle -d jaffle <<'SQL'
SELECT 'sales.customers' AS "table", count(*) AS rows FROM sales.customers
UNION ALL SELECT 'sales.stores', count(*) FROM sales.stores
UNION ALL SELECT 'sales.orders', count(*) FROM sales.orders
UNION ALL SELECT 'sales.order_items', count(*) FROM sales.order_items
UNION ALL SELECT 'catalog.products', count(*) FROM catalog.products
UNION ALL SELECT 'catalog.supplies', count(*) FROM catalog.supplies
UNION ALL SELECT 'marketing.tweets', count(*) FROM marketing.tweets;
SQL

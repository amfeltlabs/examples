#!/bin/sh
# Step 1: start the source database, and make the folders the agent writes to.
#
# The source is PostgreSQL holding the Jaffle Shop in three schemas: sales, catalog and
# marketing. Its first start loads the data; a later start finds it loaded. work/ will hold
# your project and warehouse/ the DuckDB file the agent builds.
set -eu
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

step "Checking for docker, docker compose and python3"
command -v docker >/dev/null || fail "docker is not installed"
docker compose version >/dev/null || fail "docker compose (v2 or later) is not installed"
command -v python3 >/dev/null || fail "python3 is needed to read expected/counts.yaml"

step "Pulling the source, agent and DuckDB images that are not here yet"
docker compose --progress auto --profile tools pull --policy missing

step "Making work/ for your project and warehouse/ for the DuckDB file"
mkdir -p work/warehouse warehouse

step "Starting the source database and waiting until the Jaffle Shop is loaded"
docker compose --progress auto up -d --wait source

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

#!/bin/sh
# Reset: stop the containers, remove the source database with its changes, and clear
# work/, with the project, the warehouse and the plugins installed there. Step 1 then
# starts again from the freshly loaded Jaffle Shop. .env and the amfelt CLI are kept.
set -eu
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

step "Stopping the containers and removing the source database's volume"
docker compose --progress "$SLOW_PROGRESS" --profile tools down --volumes

step "Clearing work/"
# warehouse/ is where earlier versions of these steps put the DuckDB file.
rm -rf work warehouse

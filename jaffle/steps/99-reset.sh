#!/bin/sh
# Reset: stop the containers, remove the source database with its changes, and clear
# work/ and warehouse/. Step 1 then starts again from the freshly loaded Jaffle Shop.
# .env is kept.
set -eu
# shellcheck source=lib.sh
. "$(dirname "$0")/lib.sh"

step "Stopping the containers and removing the source database's volume"
docker compose --progress auto --profile tools down --volumes --remove-orphans

step "Clearing work/ and warehouse/"
rm -rf work warehouse

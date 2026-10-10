#!/bin/sh
# Load the Jaffle corpus on a postgres container's first start. Mount this file into
# /docker-entrypoint-initdb.d/ (any name ending in .sh), and load.sql and the data where
# the two variables below point:
#
#   JAFFLE_LOAD_SQL  path of load.sql      (default /jaffle/db/load.sql)
#   JAFFLE_DATADIR   the published CSVs    (default /data/jaffle)
#
# Do not put load.sql itself in /docker-entrypoint-initdb.d/: the image would run it
# without a transaction, and load.sql refuses that. This file runs it in one.
#
# The image's entrypoint sources this file when it is not executable and runs it when it
# is, so it sets no shell options and ends with the psql call: either way a failed load
# fails the container's initialization. POSTGRES_USER and POSTGRES_DB are the image's own.
psql -X -v ON_ERROR_STOP=1 --single-transaction \
    --username "${POSTGRES_USER:-postgres}" --dbname "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}" \
    -v datadir="${JAFFLE_DATADIR:-/data/jaffle}" \
    -f "${JAFFLE_LOAD_SQL:-/jaffle/db/load.sql}"

# The Jaffle tutorial project

This folder is the tutorial's ready-made Amfelt project: the project file `amfelt.yaml` and the data contract `contracts/jaffle.odcs.yaml` it points to. It loads the Jaffle PostgreSQL source into a DuckDB warehouse at `/work/warehouse/jaffle.duckdb`, with the staging, history and bridge layers in the `jaffle_stg`, `jaffle_hst` and `jaffle_bdg` schemas.

## How it was produced

Both files were produced by the agent's own commands, the same path the tutorial walks step by step:

1. `init --name jaffle`.
2. `import jaffle <source URL> --namespace sales,catalog,marketing --abbreviation jaffle`. The three schemas are named because, without `--namespace`, import takes every other schema too: the empty `public` schema, and PostgreSQL's temporary schemas `pg_temp_2` and `pg_toast_temp_2`, left by the load's temporary table (agent#781). The flag stays needed once agent#781 is fixed, because `public` is an ordinary schema. Importing more than one schema needs `--abbreviation`.
3. `add-target warehouse duckdb --db-path /work/warehouse/jaffle.duckdb`.
4. `add-job load --source jaffle --target warehouse --include "*" --layer bridge --schema-prefix jaffle`. The job stops at the bridge layer, because this project has no business layer.

Two parts were written by hand: the `plugins` block, which names the PostgreSQL and DuckDB plugins as local before `import` ran, and `select.objects`, which gives each table its table type and history mode, and orders and tweets their event-time column. CI checks that these settings, the job's layer and its schema prefix equal the `job` block of `../expected/counts.yaml`, the settings the expected counts were made for. `tweets` is typed `transaction`, not `event`: `event` is a value from before the table types were reduced to master, transaction and reference, and it is refused where a project is authored.

Neither file holds a login: the source URL, with its user and password, is passed to `sync` on every run.

## Plugin versions

`IMAGE` pins the agent image this project was produced with, by tag and registry digest, and the plugin versions baked into it. Only the PostgreSQL plugin's version is recorded in `amfelt.yaml`: it is 4.9.1, the version the pinned image holds, updated by hand with each image pin, and the agent runs offline only with a recorded version the image has installed, so an image with another PostgreSQL plugin needs this project updated, or produced again, with it. The DuckDB plugin's version is not recorded, so it is whatever the image installs. CI checks the recorded version against `IMAGE`.

## Notes for running it

- Create the `warehouse/` directory first: the DuckDB plugin does not create the folder of its database file.
- `deploy` takes its layer from its own `--layer` flag, not from the job, and deploys only the layer it names. Deploying `--layer staging`, then `history`, then `bridge` creates exactly the three layers the job loads; a plain `deploy` also creates empty `jaffle_biz` tables.
- The validity columns hold processing time in the history tables of customers, products, stores, supplies and order_items, which have no event-time column: `amf_valid_from` is the start time of the run that opened the version, and `amf_valid_to` that of the run that closed it. The reveal is one of them: Catherine Navarro's two versions carry run 1's and run 2's start times, not a date for her move. In orders and tweets, which have an event-time column, `amf_valid_from` is the event time, `ordered_at` and `tweeted_at`. The append tables (orders, order_items and tweets) close nothing, so their `amf_valid_to` stays empty.
- The run summary's `Insert` column for the history layer currently counts the SCD2 rows a run closes as well as the rows it adds (agent#782): run 2 shows 7 for customers, which gain 4 rows and close 3.

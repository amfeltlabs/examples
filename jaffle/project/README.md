# The Jaffle tutorial project

This folder is the tutorial's ready-made Amfelt project: the project file `amfelt.yaml` and the data contract `contracts/jaffle.odcs.yaml` it points to. It loads the Jaffle PostgreSQL source into a DuckDB warehouse at `/work/warehouse/jaffle.duckdb`, with the staging, history and bridge layers in the `jaffle_stg`, `jaffle_hst` and `jaffle_bdg` schemas.

## How it was produced

Both files were produced by the agent's own commands, the same path the tutorial walks step by step:

1. `init --name jaffle`.
2. `import jaffle <source URL> --namespace sales,catalog,marketing --abbreviation jaffle`. The three schemas are named because, without `--namespace`, import also takes PostgreSQL's temporary schemas (`pg_temp_2`, `pg_toast_temp_2`, left by the load's temporary table) as business schemas (agent#781). Importing more than one schema needs `--abbreviation`.
3. `add-target warehouse duckdb --db-path /work/warehouse/jaffle.duckdb`.
4. `add-job load --source jaffle --target warehouse --include "*" --layer bridge --schema-prefix jaffle`. The job stops at the bridge layer, because this project has no business layer.

Two parts were written by hand: the `plugins` block, which names the PostgreSQL and DuckDB plugins as local before `import` ran, and `select.objects`, which gives each table its table type and history mode. `tweets` is typed `transaction`, not `event`: `event` is a value from before the table types were reduced to master, transaction and reference, and it is refused where a project is authored.

Neither file holds a login: the source URL, with its user and password, is passed to `sync` on every run.

## Plugin versions

The plugin versions are tied to the agent image. `import` recorded the PostgreSQL plugin's version, 4.2.5, the one the image holds, and the agent runs offline only with a plugin version the image has installed. An image that ships other plugin versions needs this project updated, or produced again, together with it.

## Notes for running it

- Create the `warehouse/` directory first: the DuckDB plugin does not create the folder of its database file.
- `deploy` takes its layer from its own `--layer` flag, not from the job, and deploys only the layer it names. Deploying `--layer staging`, then `history`, then `bridge` creates exactly the three layers the job loads; a plain `deploy` also creates empty `jaffle_biz` tables.
- `ifl_valid_from` and `ifl_valid_to` hold processing time, when the run loaded the row, not the time of the business event.
- The run summary's `Insert` column for the history layer currently counts the SCD2 rows a run closes as well as the rows it adds (agent#782): run 2 shows 7 for customers, which gain 4 rows and close 3.

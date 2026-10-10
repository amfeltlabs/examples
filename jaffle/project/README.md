# The Jaffle tutorial project

This folder is the tutorial's ready-made Amfelt project: the project file `amfelt.yaml` and the data contract `contracts/jaffle.odcs.yaml` it points to. It loads the Jaffle PostgreSQL source into a DuckDB warehouse at `/work/warehouse/jaffle.duckdb`, with the staging, history and bridge layers in the `jaffle_stg`, `jaffle_hst` and `jaffle_bdg` schemas.

Both files were produced by the agent's own commands, the same path the tutorial walks step by step: `init`, then `import` against the loaded source, `add-target` and `add-job`. Two parts were written by hand: the `plugins` block, which names the PostgreSQL and DuckDB plugins as local before `import` ran, and `select.objects`, which gives each table its table type and history mode. Neither file holds a login: the source URL, with its user and password, is passed to `sync` on every run.

The plugin versions are tied to the agent image. `import` recorded the PostgreSQL plugin's version, 4.2.5, the one the image holds, and the agent runs offline only with a plugin version the image has installed. An image that ships other plugin versions needs this project updated, or produced again, together with it.

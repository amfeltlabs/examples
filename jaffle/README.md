# Jaffle Shop

The Jaffle Shop dataset, and a hands-on tutorial that loads it with the Amfelt agent: from
a PostgreSQL source into a DuckDB warehouse with staging, history and bridge layers, on
your own machine, with no account.

## What you need

- Docker with Docker Compose v2.
- `python3`, which the steps use to read the published expected results.
- About 650 MB of disk for the three images, and a network connection to pull them.

## The tutorial

Run the steps from this directory, in order. Each step prints what it does, and stops with
an error when something is not as expected.

```bash
cp .env.example .env
steps/01-start.sh            # start the source database
steps/02-discover.sh         # read the source into a data contract
steps/03-configure.sh        # build the project; QUICK=1 copies the ready-made one
steps/04-run1.sh             # create the layers, load, and count
steps/05-change-source.sh    # change the source: a customer moves to Brooklyn
steps/06-run2-reveal.sh      # load again, and see the history the warehouse kept
```

`steps/99-reset.sh` removes the containers, the source database and everything the steps
wrote, so `steps/01-start.sh` starts over.

To look around the warehouse yourself between steps, open a read-only DuckDB shell with
`docker compose run --rm duckdb`. Close it before the next step: DuckDB allows one writer
and no reader beside it, so a load fails while the shell holds the file.

## What is here

| Path                  | Contents                                                      |
| --------------------- | ------------------------------------------------------------- |
| `compose.yaml`        | The source database, the agent, and a DuckDB shell            |
| `.env.example`        | The agent image and the demo password; copy it to `.env`      |
| `steps/`              | The tutorial, one script per step                             |
| `project/`            | The ready-made project the tutorial builds                    |
| `db/`, `data/`        | The source model, its data, and the changes of the second run |
| `expected/`           | The row counts each layer must hold after each run            |
| `derive/`, `release/` | How the data was derived, and how it is released              |

The steps write to `work/` (your project) and `warehouse/` (the DuckDB file). Both are
ignored by git.

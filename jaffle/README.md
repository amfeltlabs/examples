# Jaffle Shop

The Jaffle Shop dataset, and a hands-on tutorial that loads it with the Amfelt agent, the `amfelt` CLI: from a PostgreSQL source into a DuckDB warehouse with staging, history and bridge layers, on your own machine. No Amfelt account and no control plane are needed. Besides the two images, the only downloads are the CLI and the two plugins the project declares, one for PostgreSQL and one for DuckDB.

## What you need

- The `amfelt` CLI 5.0.7 on your `PATH`, on macOS or Linux. See [Installing the CLI](#installing-the-cli).
- Docker with Docker Compose 2.22 or later, which runs the source database and a DuckDB shell.
- `python3`, which the steps use to read the published expected results and to check your project against them.
- About 550 MB of disk for the two images, the CLI and its two plugins, and a network connection to download them.

## Installing the CLI

The CLI and its plugins will be published through the Amfelt release channel, with package managers for the CLI. Until that channel exists, both come from private GitHub releases, which only the Amfelt team can read. With the GitHub CLI, `gh`, signed in with read access to `amfeltlabs/agent` and `amfeltlabs/hub`:

```bash
sh steps/install-from-github.sh cli ~/.local/bin   # or any other folder on your PATH
amfelt version
```

Steps 2 and 3 install the plugins the project declares the same way, with `gh`. Once the channel exists, `amfelt plugin install` does that instead; today it needs a registered agent and a control plane.

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

`steps/99-reset.sh` removes the containers, the source database and everything the steps wrote, the installed plugins included, so `steps/01-start.sh` starts over. It keeps the `amfelt` CLI.

The steps run `amfelt` in `work/`, your project directory, so you can run its commands there yourself, for example `cd work && amfelt sync --help`. The source database listens on `127.0.0.1`, on the port `SOURCE_PORT` in `.env` names, 15432 unless you change it.

To look around the warehouse yourself between steps, open a read-only DuckDB shell with `docker compose run --rm duckdb`. Close it before the next step: DuckDB allows one writer and no reader beside it. On Linux a load therefore fails while the shell holds the file. Docker Desktop on macOS does not pass the shell's lock from its container to the agent on your machine, so there the load runs anyway, under the open shell. If you have the DuckDB CLI on your machine, `duckdb -readonly work/warehouse/jaffle.duckdb` opens the same file with a lock the agent sees.

## What is here

| Path                  | Contents                                                      |
| --------------------- | ------------------------------------------------------------- |
| `compose.yaml`        | The source database and a DuckDB shell                        |
| `.env.example`        | The demo password and the source's port; copy it to `.env`    |
| `steps/`              | The tutorial, one script per step, and the interim installer  |
| `steps/ci/`           | CI's helpers that run and check the steps; not for readers    |
| `project/`            | The ready-made project the tutorial builds                    |
| `db/`, `data/`        | The source model, its data, and the changes of the second run |
| `expected/`           | The row counts each layer must hold after each run            |
| `derive/`, `release/` | How the data was derived, and how it is released              |

The steps write to `work/`: your project, the plugins in `work/.amfelt/`, and the DuckDB file in `work/warehouse/`. It is ignored by git.

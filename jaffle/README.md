# Jaffle Shop

The Jaffle Shop dataset: seven tables of data in three schemas, and two empty tables that show a two-column foreign key and two keys into one table, in a PostgreSQL source you start on your own machine, with a DuckDB shell to look at what the Amfelt agent loads.

**The tutorial is at <https://amfelt.com/docs/quickstart/>.** You set things up in the Amfelt app, and the `amfelt` agent, which runs on your machine and not in a container, loads this dataset into DuckDB on that machine.

## Start the source

You need Docker with Docker Compose 2.22 or later, and no settings file:

```bash
git clone https://github.com/amfeltlabs/examples.git
cd examples/jaffle
docker compose up -d --wait
```

The source listens on `127.0.0.1`, port `15432`. The database and the user are `jaffle`, and so is the password: a public demo value on a port only this machine reaches. The image serves no TLS, so turn SSL off when you connect, which is acceptable for this local demo only.

On Linux the database container must be able to read `db/` and `data/`. If you cloned with a umask that keeps files private, such as `077`, it stops with `Permission denied`; run `chmod -R a+rX db data` and `docker compose down -v` first, then start it again.

If port `15432` is taken, copy `.env.example` to `.env`, set `SOURCE_PORT` there, and run `docker compose up -d --wait` again.

## Change the source

`db/run2.sql` changes the source as a day of business would. Among other things, Nikos Lefebvre moves from Utrecht to Rotterdam:

```bash
docker compose exec source psql -U jaffle -d jaffle -f /jaffle/db/run2.sql
```

Apply it once. A second time it fails, changes nothing, and says how to reload the source.

## The data

Everything here is fictional: the customers, their addresses and the shop. The street names are invented. A customer's name is drawn from two lists of common names, leaving out the well-known people we found, so a name can still match a real person's by chance. No row describes a real person or a real address. Stores in Utrecht and Rotterdam trade; Antwerp, Lyon, Bologna and Valencia are planned openings.

Amounts are integer euro cents. `subtotal` excludes VAT, `tax_rate` is the store's VAT rate, one flat rate for its whole menu, `tax_paid` is `subtotal * tax_rate`, and `order_total` is `subtotal + tax_paid`. Every order is at a Dutch store at 9%, and nothing rounds. The rates are illustrative, not tax advice.

## Look at the warehouse

The agent writes its DuckDB file to `work/warehouse/`, which is there after you clone and is otherwise ignored by git. With the file at `work/warehouse/jaffle.duckdb`, open a read-only shell on it:

```bash
docker compose run --rm duckdb
```

Close it before the next load: DuckDB allows one writer and no reader beside it. On Linux a load therefore fails while the shell holds the file. Docker Desktop on macOS does not pass the shell's lock from its container to the agent on your machine, so there the load runs anyway, under the open shell, which may then show old rows with no error, or fail. Open a new shell to read after a load.

## Clean up

```bash
docker compose down -v
```

This removes the containers and the source database. Your warehouse stays in `work/warehouse/`; delete `jaffle.duckdb` there to remove it.

## What is here

| Path                  | Contents                                                              |
| --------------------- | --------------------------------------------------------------------- |
| `compose.yaml`        | The source database and a DuckDB shell                                |
| `.env.example`        | The optional settings: the demo password and the source's port        |
| `db/`, `data/`        | The source model, its data, and the changes of the second run         |
| `expected/`           | The row counts each layer must hold after each run                    |
| `derive/`, `release/` | How the data was derived, and how it is released                      |
| `project/`            | The ready-made project CI and the stack run; not for readers          |

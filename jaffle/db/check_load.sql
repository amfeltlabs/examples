-- Check a database that load.sql has loaded, and fail unless the model is exactly the one it
-- documents. Read-only: it creates nothing but session-local temporary tables and views.
--
--   psql -X -v ON_ERROR_STOP=1 -v datadir=/path/to/jaffle/data -f jaffle/db/check_load.sql
--
-- datadir is the directory of the published CSV files, as for load.sql, with the same
-- default (/data/jaffle); the files are read client-side with \copy, from datadir as the
-- working directory, which is restored after the last file. Every check runs, each
-- failure is recorded, and the last statement raises an error listing all of them, so
-- psql exits non-zero (3, under ON_ERROR_STOP). Only when every check passes does it print
-- one ok line per check, so an ok line never stands beside a failure it could hide.
--
-- What it asserts:
--   1. the only non-system schemas besides public are sales, catalog and marketing; they
--      hold exactly the seven tables of the model, with its columns in its order; no
--      relation anywhere is named raw_*; public holds no relation, function or type;
--   2. every schema, table and column carries a non-empty comment, read from
--      pg_description (obj_description, and col_description by attnum);
--   3. each table's primary key, in column order (catalog.supplies: id, then sku), and
--      exactly the six foreign keys, by name, columns, target and target columns, all
--      validated and none cascading;
--   4. every table holds as many rows as its CSV file holds records, counted here from the
--      files, independently of load.sql's pinned counts; customer_addresses.csv holds one
--      record per customer; every customer has a non-empty street, postal code, city and
--      country.
\set QUIET on
SET search_path = pg_catalog;

\if :{?datadir}
\else
\set datadir /data/jaffle
\endif
\set jaffle_previous_dir `pwd`
\cd :datadir

CREATE TEMPORARY TABLE failures (n serial, check_name text, detail text);

-- The model: table, then its columns in order.
CREATE TEMPORARY TABLE model (sch text, tbl text, cols text[], pk text[], csv text);
INSERT INTO pg_temp.model VALUES
    ('sales', 'customers', '{id,name,street,postal_code,city,country}', '{id}', 'customers.csv'),
    ('sales', 'stores', '{id,name,opened_at,tax_rate}', '{id}', 'stores.csv'),
    ('sales', 'orders', '{id,customer_id,store_id,ordered_at,subtotal,tax_paid,order_total}', '{id}', 'orders.csv'),
    ('sales', 'order_items', '{id,order_id,sku}', '{id}', 'order_items.csv'),
    ('catalog', 'products', '{sku,name,type,price,description}', '{sku}', 'products.csv'),
    ('catalog', 'supplies', '{id,sku,name,cost,perishable}', '{id,sku}', 'supplies.csv'),
    ('marketing', 'tweets', '{id,customer_id,tweeted_at,content}', '{id}', 'tweets.csv');

-- User schemas: everything but the system schemas and the temporary ones.
CREATE TEMPORARY VIEW user_schemas AS
    SELECT n.oid, n.nspname
      FROM pg_catalog.pg_namespace AS n
     WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
       AND n.nspname NOT LIKE 'pg\_temp\_%'
       AND n.nspname NOT LIKE 'pg\_toast\_temp\_%';

-- 1. Schemas and tables.

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'schemas', format('schemas are %s, expected catalog, marketing, sales (and public)',
                         coalesce(string_agg(nspname, ', ' ORDER BY nspname), 'none'))
  FROM pg_temp.user_schemas
 WHERE nspname <> 'public'
HAVING coalesce(array_agg(nspname::text ORDER BY nspname), '{}')
       IS DISTINCT FROM '{catalog,marketing,sales}'::text[];

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'tables', format('%s %I.%I', CASE WHEN m.tbl IS NULL THEN 'unexpected relation' ELSE 'missing table' END,
                        coalesce(m.sch, a.sch), coalesce(m.tbl, a.tbl))
  FROM (SELECT n.nspname::text AS sch, c.relname::text AS tbl
          FROM pg_catalog.pg_class AS c
          JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname IN ('sales', 'catalog', 'marketing')
           AND c.relkind IN ('r', 'p', 'v', 'm', 'f')) AS a
  FULL JOIN pg_temp.model AS m ON m.sch = a.sch AND m.tbl = a.tbl
 WHERE m.tbl IS NULL OR a.tbl IS NULL;

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'tables', format('%I.%I has columns %s, expected %s', m.sch, m.tbl,
                        array_to_string(actual.cols, ', '), array_to_string(m.cols, ', '))
  FROM pg_temp.model AS m
  JOIN LATERAL (
        SELECT array_agg(a.attname::text ORDER BY a.attnum) AS cols
          FROM pg_catalog.pg_attribute AS a
         WHERE a.attrelid = to_regclass(format('%I.%I', m.sch, m.tbl))
           AND a.attnum > 0 AND NOT a.attisdropped) AS actual ON true
 WHERE to_regclass(format('%I.%I', m.sch, m.tbl)) IS NOT NULL
   AND actual.cols IS DISTINCT FROM m.cols;

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'tables', format('relation %I.%I is named raw_*', n.nspname, c.relname)
  FROM pg_catalog.pg_class AS c
  JOIN pg_temp.user_schemas AS n ON n.oid = c.relnamespace
 WHERE c.relname LIKE 'raw\_%';

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'public', format('public holds %s %s', kind, name)
  FROM (SELECT 'relation' AS kind, c.relname::text AS name
          FROM pg_catalog.pg_class AS c
         WHERE c.relnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
        UNION ALL
        SELECT 'function', p.proname::text
          FROM pg_catalog.pg_proc AS p
         WHERE p.pronamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')
        UNION ALL
        SELECT 'type', t.typname::text
          FROM pg_catalog.pg_type AS t
         WHERE t.typnamespace IN (SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = 'public')) AS stray;

-- 2. Comments, from pg_description. Columns are keyed by attnum.

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'comments', format('schema %I has no comment', n.nspname)
  FROM pg_catalog.pg_namespace AS n
 WHERE n.nspname IN ('sales', 'catalog', 'marketing')
   AND coalesce(btrim(obj_description(n.oid, 'pg_namespace')), '') = '';

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'comments', format('table %s has no comment', c.oid::regclass)
  FROM pg_catalog.pg_class AS c
 WHERE c.relnamespace IN (SELECT oid FROM pg_catalog.pg_namespace
                           WHERE nspname IN ('sales', 'catalog', 'marketing'))
   AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
   AND coalesce(btrim(obj_description(c.oid, 'pg_class')), '') = '';

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'comments', format('column %s.%I has no comment', c.oid::regclass, a.attname)
  FROM pg_catalog.pg_class AS c
  JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
 WHERE c.relnamespace IN (SELECT oid FROM pg_catalog.pg_namespace
                           WHERE nspname IN ('sales', 'catalog', 'marketing'))
   AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
   AND a.attnum > 0 AND NOT a.attisdropped
   AND coalesce(btrim(col_description(c.oid, a.attnum)), '') = '';

-- 3. Keys. A constraint's columns, in key order, by name.

CREATE TEMPORARY VIEW keys AS
    SELECT k.contype::text AS contype,
           k.conname::text AS conname,
           k.conrelid::regclass::text AS tbl,
           (SELECT array_agg(a.attname::text ORDER BY o.i)
              FROM unnest(k.conkey) WITH ORDINALITY AS o (attnum, i)
              JOIN pg_catalog.pg_attribute AS a
                ON a.attrelid = k.conrelid AND a.attnum = o.attnum) AS cols,
           CASE WHEN k.contype = 'f' THEN k.confrelid::regclass::text END AS ref_tbl,
           (SELECT array_agg(a.attname::text ORDER BY o.i)
              FROM unnest(k.confkey) WITH ORDINALITY AS o (attnum, i)
              JOIN pg_catalog.pg_attribute AS a
                ON a.attrelid = k.confrelid AND a.attnum = o.attnum) AS ref_cols,
           k.convalidated,
           k.confupdtype::text AS on_update,
           k.confdeltype::text AS on_delete
      FROM pg_catalog.pg_constraint AS k
     WHERE k.contype IN ('p', 'f')
       AND k.connamespace IN (SELECT oid FROM pg_catalog.pg_namespace
                               WHERE nspname IN ('sales', 'catalog', 'marketing'));

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'keys', format('%I.%I has primary key %s, expected (%s)', m.sch, m.tbl,
                      coalesce('(' || array_to_string(k.cols, ', ') || ')', 'none'),
                      array_to_string(m.pk, ', '))
  FROM pg_temp.model AS m
  LEFT JOIN pg_temp.keys AS k
    ON k.contype = 'p' AND k.tbl = format('%I.%I', m.sch, m.tbl)
 WHERE k.cols IS DISTINCT FROM m.pk;

-- The six foreign keys, exactly: no action on update or delete ('a'), validated.
CREATE TEMPORARY TABLE expected_fkeys (conname text, tbl text, cols text[], ref_tbl text, ref_cols text[]);
INSERT INTO pg_temp.expected_fkeys VALUES
    ('orders_customer_id_fkey', 'sales.orders', '{customer_id}', 'sales.customers', '{id}'),
    ('orders_store_id_fkey', 'sales.orders', '{store_id}', 'sales.stores', '{id}'),
    ('order_items_order_id_fkey', 'sales.order_items', '{order_id}', 'sales.orders', '{id}'),
    ('order_items_sku_fkey', 'sales.order_items', '{sku}', 'catalog.products', '{sku}'),
    ('supplies_sku_fkey', 'catalog.supplies', '{sku}', 'catalog.products', '{sku}'),
    ('tweets_customer_id_fkey', 'marketing.tweets', '{customer_id}', 'sales.customers', '{id}');

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'keys',
       CASE WHEN k.conname IS NULL THEN format('missing foreign key %s on %s', e.conname, e.tbl)
            WHEN e.conname IS NULL THEN format('unexpected foreign key %s on %s', k.conname, k.tbl)
            ELSE format('foreign key %s on %s is (%s) -> %s (%s), validated %s, on update %s, on delete %s',
                        k.conname, k.tbl, array_to_string(k.cols, ', '), k.ref_tbl,
                        array_to_string(k.ref_cols, ', '), k.convalidated, k.on_update, k.on_delete)
       END
  FROM (SELECT * FROM pg_temp.keys WHERE contype = 'f') AS k
  FULL JOIN pg_temp.expected_fkeys AS e ON e.conname = k.conname AND e.tbl = k.tbl
 WHERE k.conname IS NULL OR e.conname IS NULL
    OR k.cols IS DISTINCT FROM e.cols
    OR k.ref_tbl IS DISTINCT FROM e.ref_tbl
    OR k.ref_cols IS DISTINCT FROM e.ref_cols
    OR NOT k.convalidated
    OR k.on_update <> 'a' OR k.on_delete <> 'a';

-- 4. Rows. Each file's data records are counted by copying it, header skipped, into text
-- columns, so the count depends on the file alone, not on load.sql's types or mapping.

CREATE TEMPORARY TABLE csv_rows (c1 text, c2 text, c3 text, c4 text, c5 text, c6 text, c7 text);
CREATE TEMPORARY TABLE csv_counts (csv text PRIMARY KEY, n bigint);

\copy pg_temp.csv_rows (c1, c2) FROM 'customers.csv' WITH (FORMAT csv, HEADER true)
INSERT INTO pg_temp.csv_counts SELECT 'customers.csv', count(*) FROM pg_temp.csv_rows;
TRUNCATE pg_temp.csv_rows;
\copy pg_temp.csv_rows (c1, c2, c3, c4, c5) FROM 'customer_addresses.csv' WITH (FORMAT csv, HEADER true)
INSERT INTO pg_temp.csv_counts SELECT 'customer_addresses.csv', count(*) FROM pg_temp.csv_rows;
TRUNCATE pg_temp.csv_rows;
\copy pg_temp.csv_rows (c1, c2, c3, c4) FROM 'stores.csv' WITH (FORMAT csv, HEADER true)
INSERT INTO pg_temp.csv_counts SELECT 'stores.csv', count(*) FROM pg_temp.csv_rows;
TRUNCATE pg_temp.csv_rows;
\copy pg_temp.csv_rows (c1, c2, c3, c4, c5, c6, c7) FROM 'orders.csv' WITH (FORMAT csv, HEADER true)
INSERT INTO pg_temp.csv_counts SELECT 'orders.csv', count(*) FROM pg_temp.csv_rows;
TRUNCATE pg_temp.csv_rows;
\copy pg_temp.csv_rows (c1, c2, c3) FROM 'order_items.csv' WITH (FORMAT csv, HEADER true)
INSERT INTO pg_temp.csv_counts SELECT 'order_items.csv', count(*) FROM pg_temp.csv_rows;
TRUNCATE pg_temp.csv_rows;
\copy pg_temp.csv_rows (c1, c2, c3, c4, c5) FROM 'products.csv' WITH (FORMAT csv, HEADER true)
INSERT INTO pg_temp.csv_counts SELECT 'products.csv', count(*) FROM pg_temp.csv_rows;
TRUNCATE pg_temp.csv_rows;
\copy pg_temp.csv_rows (c1, c2, c3, c4, c5) FROM 'supplies.csv' WITH (FORMAT csv, HEADER true)
INSERT INTO pg_temp.csv_counts SELECT 'supplies.csv', count(*) FROM pg_temp.csv_rows;
TRUNCATE pg_temp.csv_rows;
\copy pg_temp.csv_rows (c1, c2, c3, c4) FROM 'tweets.csv' WITH (FORMAT csv, HEADER true)
INSERT INTO pg_temp.csv_counts SELECT 'tweets.csv', count(*) FROM pg_temp.csv_rows;
TRUNCATE pg_temp.csv_rows;
\cd :jaffle_previous_dir

DO $$
DECLARE
    m      record;
    actual bigint;
    want   bigint;
BEGIN
    FOR m IN SELECT * FROM pg_temp.model ORDER BY sch, tbl LOOP
        SELECT n INTO want FROM pg_temp.csv_counts WHERE csv = m.csv;
        IF to_regclass(format('%I.%I', m.sch, m.tbl)) IS NULL THEN
            CONTINUE;  -- already reported as a missing table
        END IF;
        EXECUTE format('SELECT count(*) FROM ONLY %I.%I', m.sch, m.tbl) INTO actual;
        IF actual IS DISTINCT FROM want THEN
            INSERT INTO pg_temp.failures (check_name, detail)
            VALUES ('rows', format('%I.%I holds %s rows, %s holds %s records',
                                   m.sch, m.tbl, actual, m.csv, want));
        END IF;
    END LOOP;

    SELECT n INTO want FROM pg_temp.csv_counts WHERE csv = 'customer_addresses.csv';
    SELECT n INTO actual FROM pg_temp.csv_counts WHERE csv = 'customers.csv';
    IF want IS DISTINCT FROM actual THEN
        INSERT INTO pg_temp.failures (check_name, detail)
        VALUES ('rows', format('customer_addresses.csv holds %s records, customers.csv %s',
                               want, actual));
    END IF;

    IF to_regclass('sales.customers') IS NOT NULL THEN
        BEGIN
            EXECUTE $q$
                SELECT count(*) FROM ONLY sales.customers
                 WHERE coalesce(btrim(street), '') = ''
                    OR coalesce(btrim(postal_code), '') = ''
                    OR coalesce(btrim(city), '') = ''
                    OR coalesce(btrim(country), '') = ''
            $q$ INTO actual;
            IF actual <> 0 THEN
                INSERT INTO pg_temp.failures (check_name, detail)
                VALUES ('rows', format('%s customer(s) lack a street, postal code, city or country',
                                       actual));
            END IF;
        EXCEPTION WHEN undefined_column THEN
            NULL;  -- already reported as a column mismatch
        END;
    END IF;
END
$$;

-- Report.

SELECT format('ok: %s', c) AS result
  FROM unnest('{schemas,tables,public,comments,keys,rows}'::text[]) WITH ORDINALITY AS u (c, i)
 WHERE NOT EXISTS (SELECT FROM pg_temp.failures)
 ORDER BY i;

DO $$
DECLARE
    report text;
BEGIN
    SELECT string_agg(format('FAIL %s: %s', check_name, detail), E'\n' ORDER BY n)
      INTO report
      FROM pg_temp.failures;
    IF report IS NOT NULL THEN
        RAISE EXCEPTION E'jaffle check: the loaded model is not as documented\n%', report;
    END IF;
END
$$;

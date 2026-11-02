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
--      hold exactly the seven tables of the model, as ordinary permanent tables and
--      nothing else, with its columns in its order and of its exact types (format_type,
--      typmods included); NOT NULL is on the primary-key columns and nowhere else; no
--      column has a default, identity or generation expression; no relation anywhere is
--      named raw_*; public holds no relation, function or type;
--   2. every schema, table and column carries a comment that holds a character other
--      than whitespace (Unicode whitespace included), read from pg_description
--      (obj_description, and col_description by attnum);
--   3. each table's primary key, in column order (catalog.supplies: id, then sku), and
--      exactly the six foreign keys, by name, columns, target and target columns, all
--      validated, not deferrable, MATCH SIMPLE and none cascading; no other constraint,
--      no index but the primary keys' and no trigger but the foreign keys' own;
--   4. every table holds as many rows as its CSV file holds records, counted here from the
--      files, independently of load.sql's pinned counts; customer_addresses.csv holds one
--      record per customer; every customer has a non-empty street, postal code, city and
--      country;
--   5. the columns with a closed form read as European: every customer lives in Utrecht, NL,
--      at a Dutch postal code, every order's VAT is its subtotal at its store's rate, with
--      nothing rounded, and every store's name is on the list of the six. It reads no free
--      text. Names, product text and tweets are scanned in the files by derive/test_corpus.py,
--      which also scans this file: a word list here would ship the words it forbids.
\set QUIET on
SET search_path = pg_catalog;

\if :{?datadir}
\else
\set datadir /data/jaffle
\endif
\set jaffle_previous_dir `pwd`
\cd :datadir

CREATE TEMPORARY TABLE failures (n serial, check_name text, detail text);

-- The model: each table with its primary key and its file, and each column with its exact
-- type as format_type() prints it, in column order.
CREATE TEMPORARY TABLE model (sch text, tbl text, pk text[], csv text);
INSERT INTO pg_temp.model VALUES
    ('sales', 'customers', '{id}', 'customers.csv'),
    ('sales', 'stores', '{id}', 'stores.csv'),
    ('sales', 'orders', '{id}', 'orders.csv'),
    ('sales', 'order_items', '{id}', 'order_items.csv'),
    ('catalog', 'products', '{sku}', 'products.csv'),
    ('catalog', 'supplies', '{id,sku}', 'supplies.csv'),
    ('marketing', 'tweets', '{id}', 'tweets.csv');

CREATE TEMPORARY TABLE model_columns (sch text, tbl text, pos int, col text, typ text);
INSERT INTO pg_temp.model_columns VALUES
    ('sales', 'customers', 1, 'id', 'uuid'),
    ('sales', 'customers', 2, 'name', 'text'),
    ('sales', 'customers', 3, 'street', 'text'),
    ('sales', 'customers', 4, 'postal_code', 'text'),
    ('sales', 'customers', 5, 'city', 'text'),
    ('sales', 'customers', 6, 'country', 'text'),
    ('sales', 'stores', 1, 'id', 'uuid'),
    ('sales', 'stores', 2, 'name', 'text'),
    ('sales', 'stores', 3, 'opened_at', 'timestamp without time zone'),
    ('sales', 'stores', 4, 'tax_rate', 'numeric(6,4)'),
    ('sales', 'orders', 1, 'id', 'uuid'),
    ('sales', 'orders', 2, 'customer_id', 'uuid'),
    ('sales', 'orders', 3, 'store_id', 'uuid'),
    ('sales', 'orders', 4, 'ordered_at', 'timestamp without time zone'),
    ('sales', 'orders', 5, 'subtotal', 'integer'),
    ('sales', 'orders', 6, 'tax_paid', 'integer'),
    ('sales', 'orders', 7, 'order_total', 'integer'),
    ('sales', 'order_items', 1, 'id', 'uuid'),
    ('sales', 'order_items', 2, 'order_id', 'uuid'),
    ('sales', 'order_items', 3, 'sku', 'text'),
    ('catalog', 'products', 1, 'sku', 'text'),
    ('catalog', 'products', 2, 'name', 'text'),
    ('catalog', 'products', 3, 'type', 'text'),
    ('catalog', 'products', 4, 'price', 'integer'),
    ('catalog', 'products', 5, 'description', 'text'),
    ('catalog', 'supplies', 1, 'id', 'text'),
    ('catalog', 'supplies', 2, 'sku', 'text'),
    ('catalog', 'supplies', 3, 'name', 'text'),
    ('catalog', 'supplies', 4, 'cost', 'integer'),
    ('catalog', 'supplies', 5, 'perishable', 'boolean'),
    ('marketing', 'tweets', 1, 'id', 'uuid'),
    ('marketing', 'tweets', 2, 'customer_id', 'uuid'),
    ('marketing', 'tweets', 3, 'tweeted_at', 'timestamp without time zone'),
    ('marketing', 'tweets', 4, 'content', 'text');

-- User schemas: everything but the system schemas and the temporary ones.
CREATE TEMPORARY VIEW user_schemas AS
    SELECT n.oid, n.nspname
      FROM pg_catalog.pg_namespace AS n
     WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
       AND n.nspname NOT LIKE 'pg\_temp\_%'
       AND n.nspname NOT LIKE 'pg\_toast\_temp\_%';

-- The tables of the three model schemas, whatever their kind, and their live columns.
CREATE TEMPORARY VIEW model_relations AS
    SELECT c.oid, n.nspname::text AS sch, c.relname::text AS tbl, c.relkind, c.relpersistence
      FROM pg_catalog.pg_class AS c
      JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
     WHERE n.nspname IN ('sales', 'catalog', 'marketing');

CREATE TEMPORARY VIEW model_attributes AS
    SELECT r.oid AS relid, r.sch, r.tbl, a.attnum, a.attname::text AS col,
           pg_catalog.format_type(a.atttypid, a.atttypmod) AS typ,
           a.attnotnull, a.atthasdef, a.attidentity, a.attgenerated
      FROM pg_temp.model_relations AS r
      JOIN pg_catalog.pg_attribute AS a ON a.attrelid = r.oid
     WHERE r.relkind NOT IN ('i', 'I', 'S')
       AND a.attnum > 0 AND NOT a.attisdropped;

-- A comment is blank unless it holds a character that is not whitespace, Unicode
-- whitespace (the no-break spaces included) and the byte-order mark counted as whitespace.
CREATE FUNCTION pg_temp.is_blank(comment text) RETURNS boolean
    LANGUAGE sql IMMUTABLE
    RETURN comment IS NULL
        OR comment ~ '^[ \t\n\v\f\r\u0085   -​    　﻿[:space:]]*$';

-- 1. Schemas, tables and columns.

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'schemas', format('schemas are %s, expected catalog, marketing, sales (and public)',
                         coalesce(string_agg(nspname, ', ' ORDER BY nspname), 'none'))
  FROM pg_temp.user_schemas
 WHERE nspname <> 'public'
HAVING coalesce(array_agg(nspname::text ORDER BY nspname), '{}')
       IS DISTINCT FROM '{catalog,marketing,sales}'::text[];

-- Every relation in the three schemas except indexes, which are checked with the
-- constraints: views, sequences and composite types count as unexpected relations.
INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'tables', format('%s %I.%I', CASE WHEN m.tbl IS NULL THEN 'unexpected relation' ELSE 'missing table' END,
                        coalesce(m.sch, r.sch), coalesce(m.tbl, r.tbl))
  FROM (SELECT * FROM pg_temp.model_relations WHERE relkind NOT IN ('i', 'I')) AS r
  FULL JOIN pg_temp.model AS m ON m.sch = r.sch AND m.tbl = r.tbl
 WHERE m.tbl IS NULL OR r.tbl IS NULL;

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'tables', format('%I.%I is relkind %s, persistence %s; expected an ordinary permanent table (r, p)',
                        r.sch, r.tbl, r.relkind, r.relpersistence)
  FROM pg_temp.model_relations AS r
  JOIN pg_temp.model AS m ON m.sch = r.sch AND m.tbl = r.tbl
 WHERE r.relkind <> 'r' OR r.relpersistence <> 'p';

-- Names, order and exact types, typmods included: numeric(6,4) is not numeric, and
-- timestamp(0) without time zone is not timestamp without time zone.
INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'columns', format('%I.%I has columns (%s), expected (%s)', m.sch, m.tbl,
                         array_to_string(actual.cols, ', '), array_to_string(want.cols, ', '))
  FROM pg_temp.model AS m
  JOIN LATERAL (
        SELECT array_agg(format('%s %s', a.col, a.typ) ORDER BY a.attnum) AS cols
          FROM pg_temp.model_attributes AS a
         WHERE a.sch = m.sch AND a.tbl = m.tbl) AS actual ON true
  JOIN LATERAL (
        SELECT array_agg(format('%s %s', c.col, c.typ) ORDER BY c.pos) AS cols
          FROM pg_temp.model_columns AS c
         WHERE c.sch = m.sch AND c.tbl = m.tbl) AS want ON true
 WHERE to_regclass(format('%I.%I', m.sch, m.tbl)) IS NOT NULL
   AND actual.cols IS DISTINCT FROM want.cols;

-- NOT NULL exactly on the primary-key columns: every other column stays nullable.
INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'columns', format('%I.%I.%I is %s, expected %s', a.sch, a.tbl, a.col,
                         CASE WHEN a.attnotnull THEN 'NOT NULL' ELSE 'nullable' END,
                         CASE WHEN a.col = ANY (m.pk) THEN 'NOT NULL (primary key)' ELSE 'nullable' END)
  FROM pg_temp.model_attributes AS a
  JOIN pg_temp.model AS m ON m.sch = a.sch AND m.tbl = a.tbl
 WHERE a.attnotnull IS DISTINCT FROM (a.col = ANY (m.pk));

-- No default, identity or generated column.
INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'columns', format('%I.%I.%I has a default, identity or generation expression', a.sch, a.tbl, a.col)
  FROM pg_temp.model_attributes AS a
 WHERE a.atthasdef OR a.attidentity <> '' OR a.attgenerated <> '';

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
   AND pg_temp.is_blank(obj_description(n.oid, 'pg_namespace'));

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'comments', format('table %I.%I has no comment', r.sch, r.tbl)
  FROM pg_temp.model_relations AS r
 WHERE r.relkind IN ('r', 'p', 'v', 'm', 'f')
   AND pg_temp.is_blank(obj_description(r.oid, 'pg_class'));

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'comments', format('column %I.%I.%I has no comment', a.sch, a.tbl, a.col)
  FROM pg_temp.model_attributes AS a
 WHERE pg_temp.is_blank(col_description(a.relid, a.attnum));

-- 3. Keys and constraints. A constraint's columns, in key order, by name.

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
           k.condeferrable,
           k.condeferred,
           k.confmatchtype::text AS match_type,
           k.confupdtype::text AS on_update,
           k.confdeltype::text AS on_delete
      FROM pg_catalog.pg_constraint AS k
     WHERE k.connamespace IN (SELECT oid FROM pg_catalog.pg_namespace
                               WHERE nspname IN ('sales', 'catalog', 'marketing'));

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'keys', format('%I.%I has primary key %s, expected (%s)', m.sch, m.tbl,
                      coalesce('(' || array_to_string(k.cols, ', ') || ')', 'none'),
                      array_to_string(m.pk, ', '))
  FROM pg_temp.model AS m
  LEFT JOIN pg_temp.keys AS k
    ON k.contype = 'p' AND k.tbl = format('%I.%I', m.sch, m.tbl)
 WHERE k.cols IS DISTINCT FROM m.pk
    OR k.condeferrable;

-- No constraint but the primary and foreign keys: no UNIQUE, CHECK, EXCLUDE or constraint
-- trigger. PostgreSQL 18 also records NOT NULL as a constraint (contype n); nullability is
-- checked above, from attnotnull, so those rows are not counted here.
INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'constraints', format('unexpected %s constraint %s on %s', k.contype, k.conname, k.tbl)
  FROM pg_temp.keys AS k
 WHERE k.contype NOT IN ('p', 'f', 'n');

-- No index but the primary keys' own, and no trigger but the foreign keys' internal ones.
INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'constraints', format('unexpected index %s on %I.%I', i.indexrelid::regclass, r.sch, r.tbl)
  FROM pg_catalog.pg_index AS i
  JOIN pg_temp.model_relations AS r ON r.oid = i.indrelid
 WHERE NOT i.indisprimary;

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'constraints', format('unexpected trigger %s on %I.%I', t.tgname, r.sch, r.tbl)
  FROM pg_catalog.pg_trigger AS t
  JOIN pg_temp.model_relations AS r ON r.oid = t.tgrelid
 WHERE NOT t.tgisinternal;

-- The six foreign keys, exactly: validated, not deferrable, MATCH SIMPLE ('s'), and no
-- action on update or delete ('a').
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
            ELSE format('foreign key %s on %s is (%s) -> %s (%s), validated %s, deferrable %s, '
                        'match %s, on update %s, on delete %s',
                        k.conname, k.tbl, array_to_string(k.cols, ', '), k.ref_tbl,
                        array_to_string(k.ref_cols, ', '), k.convalidated, k.condeferrable,
                        k.match_type, k.on_update, k.on_delete)
       END
  FROM (SELECT * FROM pg_temp.keys WHERE contype = 'f') AS k
  FULL JOIN pg_temp.expected_fkeys AS e ON e.conname = k.conname AND e.tbl = k.tbl
 WHERE k.conname IS NULL OR e.conname IS NULL
    OR k.cols IS DISTINCT FROM e.cols
    OR k.ref_tbl IS DISTINCT FROM e.ref_tbl
    OR k.ref_cols IS DISTINCT FROM e.ref_cols
    OR NOT k.convalidated
    OR k.condeferrable OR k.condeferred
    OR k.match_type <> 's'
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

-- 5. The columns with a closed form read as European. Each check is skipped when an earlier
-- one already reported its table or column as missing.

DO $$
BEGIN
    INSERT INTO pg_temp.failures (check_name, detail)
    SELECT 'europe', format('customer %s lives at %s, %s, %s, %s', c.id, c.street, c.postal_code, c.city, c.country)
      FROM ONLY sales.customers AS c
     WHERE c.city IS DISTINCT FROM 'Utrecht'
        OR c.country IS DISTINCT FROM 'NL'
        OR c.postal_code !~ '^[1-9][0-9]{3} [A-Z]{2}$';

    INSERT INTO pg_temp.failures (check_name, detail)
    SELECT 'europe', format('order %s: subtotal %s, tax_paid %s, order_total %s at tax_rate %s',
                            o.id, o.subtotal, o.tax_paid, o.order_total, s.tax_rate)
      FROM ONLY sales.orders AS o
      JOIN ONLY sales.stores AS s ON s.id = o.store_id
     WHERE o.order_total IS DISTINCT FROM o.subtotal + o.tax_paid
        OR o.tax_paid::numeric IS DISTINCT FROM o.subtotal * s.tax_rate;

    INSERT INTO pg_temp.failures (check_name, detail)
    SELECT 'europe', format('store %s has tax_rate %s', s.name, s.tax_rate)
      FROM ONLY sales.stores AS s
      LEFT JOIN (VALUES ('Utrecht', 0.09), ('Rotterdam', 0.09), ('Antwerp', 0.12),
                        ('Lyon', 0.10), ('Bologna', 0.10), ('Valencia', 0.10)) AS w (name, rate)
        ON w.name = s.name
     WHERE w.rate IS DISTINCT FROM s.tax_rate;
EXCEPTION WHEN undefined_table OR undefined_column THEN
    NULL;  -- already reported as a missing table or a column mismatch
END
$$;

-- Report.

SELECT format('ok: %s', c) AS result
  FROM unnest('{schemas,tables,columns,public,comments,keys,constraints,rows,europe}'::text[]) WITH ORDINALITY AS u (c, i)
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

-- Print the source's state in the form expected/counts.py prints its expectation, so the
-- two can be compared with diff:
--
--   psql -X -v ON_ERROR_STOP=1 -v mover=<uuid> -v deleted=<uuid> \
--        -f jaffle/db/source_counts.sql
--   python3 jaffle/expected/counts.py expect run1     # or run2
--
-- One line per table, "source", the schema-qualified table and its row count, sorted by
-- table name in byte order; then one line for each customer of the history reveal that the
-- source holds: "mover" or "deleted", the id, the name and the four address columns.
-- Fields are separated by tabs. mover and deleted are psql variables holding the two ids
-- (counts.py get reveal.customer_id, reveal.deleted.customer_id). A customer the source
-- does not hold prints no line: the deleted customer's line is expected after run 1 only.
--
-- Read-only. The search path is pinned to pg_catalog, so every name resolves as written.
\set ON_ERROR_STOP on
\set QUIET on
\pset format unaligned
\pset tuples_only on
SET search_path = pg_catalog;

SELECT pg_catalog.concat_ws(E'\t', 'source', t.tbl, t.n)
  FROM (
        SELECT 'sales.customers' AS tbl, (SELECT count(*) FROM ONLY sales.customers) AS n
        UNION ALL SELECT 'sales.stores', (SELECT count(*) FROM ONLY sales.stores)
        UNION ALL SELECT 'sales.orders', (SELECT count(*) FROM ONLY sales.orders)
        UNION ALL SELECT 'sales.order_items', (SELECT count(*) FROM ONLY sales.order_items)
        UNION ALL SELECT 'catalog.products', (SELECT count(*) FROM ONLY catalog.products)
        UNION ALL SELECT 'catalog.supplies', (SELECT count(*) FROM ONLY catalog.supplies)
        UNION ALL SELECT 'marketing.tweets', (SELECT count(*) FROM ONLY marketing.tweets)
       ) AS t
 ORDER BY t.tbl COLLATE "C";

SELECT pg_catalog.concat_ws(E'\t', 'mover', c.id, c.name, c.street, c.postal_code, c.city, c.country)
  FROM sales.customers AS c
 WHERE c.id = :'mover';

SELECT pg_catalog.concat_ws(E'\t', 'deleted', c.id, c.name, c.street, c.postal_code, c.city, c.country)
  FROM sales.customers AS c
 WHERE c.id = :'deleted';

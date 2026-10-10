-- Check the source after db/run2.sql: the facts its row counts cannot show. Read-only: it
-- creates nothing but a session-local temporary table.
--
--   psql -X -v ON_ERROR_STOP=1 -v mover=<uuid> -f jaffle/db/check_run2.sql
--
-- mover is the id of the customer who moves to Brooklyn (counts.py get reveal.customer_id).
-- Every check runs and records its failures; the last statement raises an error listing
-- them all, so psql exits non-zero, and only when none failed does it print its ok lines.
-- expected/counts.yaml's source counts and the mover's address are compared separately,
-- with db/source_counts.sql.
--
-- What it asserts:
--   1. every order, the corpus's and run 2's, charges order_total = subtotal + tax_paid,
--      and tax_paid follows the corpus's own rule, int(subtotal / 100 * tax_rate * 100) in
--      double precision, at the rate of the store it was placed at;
--   2. every order run 2 adds (ids starting d0000000-) has items, and its subtotal is the
--      sum of their current prices: they are priced after the JAF-004 change;
--   3. no order is placed before its store's opened_at. This is what keeps the comment on
--      sales.stores.opened_at true: a store whose opening is after the order window has
--      not traded yet. After run 2 the window reaches Brooklyn's opening day; Chicago, San
--      Francisco, New Orleans and Los Angeles still open after it and hold no order;
--   4. the history reveal's beat: Brooklyn's first order is the mover's, on Brooklyn's
--      opening day, and the mover still holds the orders run 1 gave them.
\set ON_ERROR_STOP on
\set QUIET on
SET search_path = pg_catalog;

CREATE TEMPORARY TABLE failures (n serial, check_name text, detail text);

-- 1. Amounts and tax.
INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'order amounts',
       pg_catalog.format('order %s: subtotal %s, tax_paid %s, order_total %s at tax_rate %s',
                         o.id, o.subtotal, o.tax_paid, o.order_total, s.tax_rate)
  FROM sales.orders AS o
  JOIN sales.stores AS s ON s.id = o.store_id
 WHERE o.order_total IS DISTINCT FROM o.subtotal + o.tax_paid
    OR o.tax_paid IS DISTINCT FROM
       pg_catalog.trunc(o.subtotal::float8 / 100 * s.tax_rate::float8 * 100)::integer
 ORDER BY o.id;

-- 2. Run 2's orders are priced from their items.
INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'run-2 order subtotals',
       pg_catalog.format('order %s: subtotal %s, its %s item(s) price at %s',
                         o.id, o.subtotal, pg_catalog.count(i.id), pg_catalog.sum(p.price))
  FROM sales.orders AS o
  LEFT JOIN sales.order_items AS i ON i.order_id = o.id
  LEFT JOIN catalog.products AS p ON p.sku = i.sku
 WHERE o.id::text LIKE 'd0000000-%'
 GROUP BY o.id, o.subtotal
HAVING pg_catalog.count(i.id) = 0
    OR pg_catalog.sum(p.price) IS DISTINCT FROM o.subtotal
 ORDER BY o.id;

-- 3. No order before its store opened.
INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'store openings',
       pg_catalog.format('order %s at %s, %s, before the store opened on %s',
                         o.id, s.name, o.ordered_at, s.opened_at)
  FROM sales.orders AS o
  JOIN sales.stores AS s ON s.id = o.store_id
 WHERE o.ordered_at < s.opened_at
 ORDER BY o.id;

-- 4. The beat: the mover opens Brooklyn.
INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'Brooklyn''s first order', d.detail
  FROM (
        SELECT CASE
                 WHEN f.id IS NULL THEN 'Brooklyn has no order'
                 WHEN f.customer_id IS DISTINCT FROM :'mover'::uuid
                   THEN pg_catalog.format('order %s is by customer %s, not the mover %s',
                                          f.id, f.customer_id, :'mover')
                 WHEN f.ordered_at::date IS DISTINCT FROM s.opened_at::date
                   THEN pg_catalog.format('order %s is on %s, not on the opening day %s',
                                          f.id, f.ordered_at::date, s.opened_at::date)
               END AS detail
          FROM sales.stores AS s
          LEFT JOIN LATERAL (
                SELECT o.id, o.customer_id, o.ordered_at
                  FROM sales.orders AS o
                 WHERE o.store_id = s.id
                 ORDER BY o.ordered_at, o.id
                 LIMIT 1
               ) AS f ON true
         WHERE s.name = 'Brooklyn'
       ) AS d
 WHERE d.detail IS NOT NULL;

INSERT INTO pg_temp.failures (check_name, detail)
SELECT 'the mover''s orders',
       pg_catalog.format('customer %s holds %s order(s) outside Brooklyn; run 1 gave them orders',
                         :'mover', pg_catalog.count(o.id))
  FROM sales.orders AS o
  JOIN sales.stores AS s ON s.id = o.store_id
 WHERE o.customer_id = :'mover'::uuid
   AND s.name <> 'Brooklyn'
HAVING pg_catalog.count(o.id) = 0;

DO $$
DECLARE
    report text;
BEGIN
    SELECT pg_catalog.string_agg(pg_catalog.format('  %s: %s', check_name, detail), E'\n' ORDER BY n)
      INTO report
      FROM pg_temp.failures;
    IF report IS NOT NULL THEN
        RAISE EXCEPTION E'jaffle run 2 check failed:\n%', report;
    END IF;
END
$$;

\pset format unaligned
\pset tuples_only on
SELECT 'ok: order amounts and tax, at every store';
SELECT 'ok: run 2''s orders are priced from their items';
SELECT 'ok: no order precedes its store''s opening';
SELECT 'ok: Brooklyn''s first order is the mover''s, on its opening day';

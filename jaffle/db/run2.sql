-- Run 2 of the Jaffle corpus: the changes that arrive after the pinned quarter, and the
-- source of the tutorial's history reveal. Apply it once, to a database load.sql has
-- loaded:
--
--   psql -X -v ON_ERROR_STOP=1 -f jaffle/db/run2.sql
--
-- It is hand-authored against the pinned corpus, never generated: jafgen draws new rows on
-- every run, and a delta that changed between runs could not back a count. It is the
-- stack's goldenset delta (validation/goldensets/mutations/jaffle-run2-delta.sql) carried
-- over to this model's names, with every date moved forward by the same 2,555 days as the
-- corpus (2018-12-01 becomes 2025-11-29, 2019-03-12 becomes 2026-03-10; times of day are
-- unchanged), new customers given addresses, and one beat added: an existing customer
-- moves to Brooklyn and places the store's first order. expected/counts.yaml holds the
-- counts this file produces, and its header shows the arithmetic.
--
-- The whole file is ONE transaction: it opens and commits its own, so a failure anywhere
-- changes nothing. It needs no --single-transaction, unlike load.sql; with it, psql only
-- warns that a transaction is already in progress. Applied twice, or to a database that already holds its rows, the first
-- duplicate id fails it. ON_ERROR_STOP is turned on here as well, so psql stops at the
-- first error even when the caller forgot the flag.
--
-- Every object is named with its schema, and the search path is pinned to pg_catalog for
-- the transaction, so nothing resolves through a schema the caller happens to have. No
-- foreign key cascades: each delete removes the children before their parent, and a
-- delete in the wrong order fails on the foreign key instead of removing rows silently.
--
-- What changes, and the history mode each table is loaded with:
--
--   sales.customers    scd2    2 inserts, 2 updates (a rename, a move), 1 delete
--   catalog.products   scd2    1 update
--   sales.orders       append  4 inserts, 2 deletes
--   sales.order_items  append  6 inserts, 3 deletes
--   marketing.tweets   append  2 inserts, 1 delete
--   sales.stores, catalog.supplies   untouched
--
-- No UPDATE touches an append table: append history keeps the first version of a row and
-- skips a changed one as a conflict, so an update there would be wrong data, not history.
-- Each updated customer changes in one way only, so each new SCD2 version shows exactly
-- one change: Kristin Frank is renamed and does not move; the customer who moves keeps
-- their name.
--
-- New rows carry ids that start d0000000-, so a reader can pick them out of any layer.
-- Money is integer cents. A new order's subtotal is the sum of its items' current prices,
-- and its tax_paid follows the corpus's own rule, int(subtotal / 100 * tax_rate * 100) in
-- floating point, at its store's tax_rate; every one of the 6,837 loaded orders satisfies
-- it. Addresses follow customer_addresses.csv: house number and street in USPS style, a
-- 5-digit ZIP code, the city, and the country as ISO 3166-1 alpha-2.
--
-- THE MOVE
--
-- Catherine Navarro (8224248d-b37a-4e73-8571-24b7d3435150) moves from 947 Spruce St,
-- Philadelphia 19118, to 418 Atlantic Ave, Brooklyn 11217, and places the first order at
-- the Brooklyn store on its opening day. She is chosen by a rule, not by hand: the
-- customers with the most orders in the pinned quarter, among those no other change in this
-- file touches, are a six-way tie at 66 orders; she has the lowest id of the six. A
-- regular is the believable customer to follow a new store across the river, and the rule
-- leaves out Jessica Lewis (deleted), Kristin Frank (renamed, and her tweet deleted) and
-- Melvin Ingram (his order voided). The query that applies it, on the loaded corpus:
--
--   SELECT o.customer_id, count(*) FROM sales.orders AS o
--    WHERE o.customer_id NOT IN ('09790bf5-a40a-4b7a-8446-a1a224fcb07b',
--                                '6350f2b4-88cb-4522-ab80-bce28ec10633',
--                                'e15678d3-3632-4e50-a703-f0ff9014081d')
--    GROUP BY o.customer_id ORDER BY count(*) DESC, o.customer_id LIMIT 1;
--
-- Her name does not change and her past orders stay where they were placed. Her address
-- is an update of an existing row, so SCD2 history closes the Philadelphia version and
-- opens the Brooklyn one; expected/counts.yaml's reveal block records both.

\set ON_ERROR_STOP on

BEGIN;

SET LOCAL search_path = pg_catalog;

-- -----------------------------------------------------------------------------
-- DELETES, children before parents
-- -----------------------------------------------------------------------------

-- 1. Jessica Lewis closes her account. She placed one order, on 2025-11-28 at 08:17 (an
--    adele-ade), and never tweeted.
--    SCD2 effect: her history row is closed, so she leaves the active view and stays in
--    history. Her order and its item go first; append history keeps those withdrawn facts.
DELETE FROM sales.order_items
 WHERE order_id IN (SELECT id FROM sales.orders
                     WHERE customer_id = '09790bf5-a40a-4b7a-8446-a1a224fcb07b');
DELETE FROM sales.orders WHERE customer_id = '09790bf5-a40a-4b7a-8446-a1a224fcb07b';
DELETE FROM sales.customers WHERE id = '09790bf5-a40a-4b7a-8446-a1a224fcb07b';

-- 2. The quarter's last order (Melvin Ingram, 2025-11-28 19:27, a tangaroo and a
--    nutellaphone who dis?) is voided, and its two items with it.
--    Append effect: nothing is closed. The order and both items stay in history and in
--    the active view; the warehouse keeps a fact the source has withdrawn.
DELETE FROM sales.order_items WHERE order_id = '7dd3d972-d159-418b-bc33-b2db228eb694';
DELETE FROM sales.orders WHERE id = '7dd3d972-d159-418b-bc33-b2db228eb694';

-- 3. Kristin Frank deletes her first tweet (2025-08-30, "This place is gross.").
--    Append effect: the tweet stays in history and in the active view.
DELETE FROM marketing.tweets WHERE id = '73388773-d088-43ab-861a-f5238057e401';

-- -----------------------------------------------------------------------------
-- UPDATES, master and reference tables only
-- -----------------------------------------------------------------------------

-- 4. Kristin Frank changes her name to Kristin Frank-Ellis. Her address does not change.
--    SCD2 effect: the Kristin Frank row is closed and a Kristin Frank-Ellis row opens.
UPDATE sales.customers
   SET name = 'Kristin Frank-Ellis'
 WHERE id = '6350f2b4-88cb-4522-ab80-bce28ec10633';

-- 5. Catherine Navarro moves to Brooklyn (see THE MOVE). Her name and country do not
--    change.
--    SCD2 effect: the Philadelphia row is closed and a Brooklyn row opens.
UPDATE sales.customers
   SET street = '418 Atlantic Ave',
       postal_code = '11217',
       city = 'Brooklyn'
 WHERE id = '8224248d-b37a-4e73-8571-24b7d3435150';

-- 6. The flame impala (JAF-004) goes up from 14.00 to 15.00.
--    SCD2 effect: the 1400 version is closed and a 1500 version opens. The orders already
--    placed keep the prices they were charged; the new ones below pay 1500.
UPDATE catalog.products
   SET price = 1500
 WHERE sku = 'JAF-004';

-- -----------------------------------------------------------------------------
-- INSERTS, parents before children
-- -----------------------------------------------------------------------------

-- 7. Two first-time customers. Priya's first order is at the Brooklyn store, so she lives
--    in Brooklyn; Tomas's is in Philadelphia, so he lives there.
INSERT INTO sales.customers (id, name, street, postal_code, city, country) VALUES
    ('d0000000-0000-4000-8000-000000000001', 'Priya Natarajan',
     '120 Court St', '11201', 'Brooklyn', 'US'),
    ('d0000000-0000-4000-8000-000000000002', 'Tomas Alvarez',
     '2215 Walnut St', '19103', 'Philadelphia', 'US');

-- 8. Four orders.
--    - Brooklyn's first order, on its opening day (sales.stores.opened_at 2026-03-10), the
--      first order at any store but Philadelphia: Catherine, at 07:42, early as her
--      Philadelphia orders were, a for richer or pourover, 700, tax at 0.04 = 28.
--    - Brooklyn's second, later that morning: Priya, a flame impala at the new price and a
--      tangaroo, 1500 + 600 = 2100, tax at 0.04 = 84.
--    - Tomas's first order, in Philadelphia: a nutellaphone who dis?, 1100, tax at
--      0.06 = 65.
--    - Kristin, under her new name, in Philadelphia: a flame impala and a chai and mighty,
--      1500 + 500 = 2000, tax at 0.06 = 120.
INSERT INTO sales.orders (id, customer_id, store_id, ordered_at, subtotal, tax_paid, order_total) VALUES
    ('d0000000-0000-4000-8000-000000000104', '8224248d-b37a-4e73-8571-24b7d3435150',
     'c9471206-c14b-46c1-a93b-fe94ee386b24', '2026-03-10 07:42:00', 700, 28, 728),
    ('d0000000-0000-4000-8000-000000000101', 'd0000000-0000-4000-8000-000000000001',
     'c9471206-c14b-46c1-a93b-fe94ee386b24', '2026-03-10 11:05:00', 2100, 84, 2184),
    ('d0000000-0000-4000-8000-000000000102', 'd0000000-0000-4000-8000-000000000002',
     '2efff8c6-64e3-4e79-8257-c78ffd3395d0', '2025-11-29 09:12:00', 1100, 65, 1165),
    ('d0000000-0000-4000-8000-000000000103', '6350f2b4-88cb-4522-ab80-bce28ec10633',
     '2efff8c6-64e3-4e79-8257-c78ffd3395d0', '2025-11-29 12:40:00', 2000, 120, 2120);

-- 9. The six items of those orders.
INSERT INTO sales.order_items (id, order_id, sku) VALUES
    ('d0000000-0000-4000-8000-000000000206', 'd0000000-0000-4000-8000-000000000104', 'BEV-004'),
    ('d0000000-0000-4000-8000-000000000201', 'd0000000-0000-4000-8000-000000000101', 'JAF-004'),
    ('d0000000-0000-4000-8000-000000000202', 'd0000000-0000-4000-8000-000000000101', 'BEV-001'),
    ('d0000000-0000-4000-8000-000000000203', 'd0000000-0000-4000-8000-000000000102', 'JAF-001'),
    ('d0000000-0000-4000-8000-000000000204', 'd0000000-0000-4000-8000-000000000103', 'JAF-004'),
    ('d0000000-0000-4000-8000-000000000205', 'd0000000-0000-4000-8000-000000000103', 'BEV-002');

-- 10. Two tweets about the new orders, in the corpus's own form.
INSERT INTO marketing.tweets (id, customer_id, tweeted_at, content) VALUES
    ('d0000000-0000-4000-8000-000000000301', 'd0000000-0000-4000-8000-000000000001',
     '2026-03-10 11:31:07.412903',
     'Jaffles from the Jaffle Shop are the best! Ordered a flame impala and a tangaroo.'),
    ('d0000000-0000-4000-8000-000000000302', '6350f2b4-88cb-4522-ab80-bce28ec10633',
     '2025-11-29 13:02:44.518207',
     'Jaffle Shop again. Ordered a flame impala and a chai and mighty. This place is the worst.');

COMMIT;

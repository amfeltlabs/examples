-- Run 2 of the Jaffle corpus: the changes that arrive after the pinned quarter, and the
-- source of the tutorial's history reveal. Apply it once, to a database load.sql has
-- loaded (and see below for what a second time does):
--
--   psql -X -v ON_ERROR_STOP=1 -f jaffle/db/run2.sql
--
-- It is hand-authored against the pinned corpus, never generated: jafgen draws new rows on
-- every run, and a delta that changed between runs could not back a count. It is the
-- stack's goldenset delta (validation/goldensets/mutations/jaffle-run2-delta.sql) carried
-- over to this model's names, with every date moved forward by the same 2,555 days as the
-- corpus (2018-12-01 becomes 2025-11-29, 2019-03-12 becomes 2026-03-10; times of day are
-- unchanged), new customers given addresses, and one beat added: an existing customer
-- moves to Rotterdam and places the store's first order. derive/localize.py's names, cities
-- and VAT apply here too. expected/counts.yaml holds the counts this file produces, and its
-- header shows the arithmetic.
--
-- The whole file is ONE transaction: it opens and commits its own, so a failure anywhere
-- changes nothing. It needs no --single-transaction, unlike load.sql; with it, psql only
-- warns that a transaction is already in progress. Applied twice, the check after BEGIN
-- fails it, changes nothing, and says how to reload the source; to a database that holds
-- only some of its rows, the first duplicate id fails it. ON_ERROR_STOP is turned on here
-- as well, so psql stops at the first error even when the caller forgot the flag.
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
-- No UPDATE touches an append table. Append history inserts only the rows whose key it
-- has not seen (change action 1, an insert) and closes nothing; a changed row is never
-- appended and its first version stays as it was, so an update there would be wrong data,
-- not history.
-- Each updated customer changes in one way only, so each new SCD2 version shows exactly
-- one change: Julia Lefebvre is renamed and does not move; the customer who moves keeps
-- his name.
--
-- New rows carry ids that start d0000000-, so a reader can pick them out of any layer.
-- Money is integer euro cents. A new order's subtotal is the sum of its items' current
-- prices, excluding VAT, and its tax_paid is exactly subtotal * its store's tax_rate, as in
-- every one of the 6,837 loaded orders. Addresses follow customer_addresses.csv: an invented
-- street, then the house number, a Dutch postal code, the city, and the country as ISO 3166-1
-- alpha-2.
--
-- THE MOVE
--
-- Nikos Lefebvre (8224248d-b37a-4e73-8571-24b7d3435150) moves from Kruimelsteeg 58, 3585 LG
-- Utrecht, to Honingsingel 41, 3021 KV Rotterdam, and places the first order at the
-- Rotterdam store on its opening day. He is chosen by a rule, not by hand: the customers
-- with the most orders in the pinned quarter, among those no other change in this file
-- touches, are a six-way tie at 66 orders; he has the lowest id of the six. A regular is the
-- believable customer to follow a new store to another city, and the rule leaves out Anouk
-- Kowalski (deleted), Julia Lefebvre (renamed, and her tweet deleted) and Inês Jacobs (her
-- order voided). The query that applies it, on the loaded corpus:
--
--   SELECT o.customer_id, count(*) FROM sales.orders AS o
--    WHERE o.customer_id NOT IN ('09790bf5-a40a-4b7a-8446-a1a224fcb07b',
--                                '6350f2b4-88cb-4522-ab80-bce28ec10633',
--                                'e15678d3-3632-4e50-a703-f0ff9014081d')
--    GROUP BY o.customer_id ORDER BY count(*) DESC, o.customer_id LIMIT 1;
--
-- His name does not change and his past orders stay where they were placed. His address
-- is an update of an existing row, so SCD2 history closes the Utrecht version and opens the
-- Rotterdam one; expected/counts.yaml's reveal block records both.

\set ON_ERROR_STOP on

BEGIN;

SET LOCAL search_path = pg_catalog;

-- The whole file commits or nothing does, so one of its new rows tells that it was applied.
DO $check$
BEGIN
    IF EXISTS (SELECT FROM sales.customers WHERE id = 'd0000000-0000-4000-8000-000000000001') THEN
        RAISE EXCEPTION 'db/run2.sql is already applied to this database, so it changed nothing'
            USING HINT = 'To apply it again from jaffle/, reload the source first: docker compose down -v, then docker compose up -d --wait';
    END IF;
END
$check$;

-- -----------------------------------------------------------------------------
-- DELETES, children before parents
-- -----------------------------------------------------------------------------

-- 1. Anouk Kowalski closes her account. She placed one order, on 2025-11-28 at 08:17 (a
--    kiwi cooler), and never tweeted.
--    SCD2 effect: her history row is closed, so she leaves the active view and stays in
--    history. Her order and its item go first; append history keeps those withdrawn facts.
DELETE FROM sales.order_items
 WHERE order_id IN (SELECT id FROM sales.orders
                     WHERE customer_id = '09790bf5-a40a-4b7a-8446-a1a224fcb07b');
DELETE FROM sales.orders WHERE customer_id = '09790bf5-a40a-4b7a-8446-a1a224fcb07b';
DELETE FROM sales.customers WHERE id = '09790bf5-a40a-4b7a-8446-a1a224fcb07b';

-- 2. The quarter's last order (Inês Jacobs, 2025-11-28 19:27, a tangaroo and a hazelnut
--    hotline) is voided, and its two items with it.
--    Append effect: nothing is closed. The order and both items stay in history and in
--    the active view; the warehouse keeps a fact the source has withdrawn.
DELETE FROM sales.order_items WHERE order_id = '7dd3d972-d159-418b-bc33-b2db228eb694';
DELETE FROM sales.orders WHERE id = '7dd3d972-d159-418b-bc33-b2db228eb694';

-- 3. Julia Lefebvre deletes her first tweet (2025-08-30, "This place is gross.").
--    Append effect: the tweet stays in history and in the active view.
DELETE FROM marketing.tweets WHERE id = '73388773-d088-43ab-861a-f5238057e401';

-- -----------------------------------------------------------------------------
-- UPDATES, master and reference tables only
-- -----------------------------------------------------------------------------

-- 4. Julia Lefebvre changes her name to Julia Lefebvre-Bakker. Her address does not change.
--    SCD2 effect: the Julia Lefebvre row is closed and a Julia Lefebvre-Bakker row opens.
UPDATE sales.customers
   SET name = 'Julia Lefebvre-Bakker'
 WHERE id = '6350f2b4-88cb-4522-ab80-bce28ec10633';

-- 5. Nikos Lefebvre moves to Rotterdam (see THE MOVE). His name and country do not
--    change.
--    SCD2 effect: the Utrecht row is closed and a Rotterdam row opens.
UPDATE sales.customers
   SET street = 'Honingsingel 41',
       postal_code = '3021 KV',
       city = 'Rotterdam'
 WHERE id = '8224248d-b37a-4e73-8571-24b7d3435150';

-- 6. The flame fandango (JAF-004) goes up from 14.00 to 15.00.
--    SCD2 effect: the 1400 version is closed and a 1500 version opens. The orders already
--    placed keep the prices they were charged; the new ones below pay 1500.
UPDATE catalog.products
   SET price = 1500
 WHERE sku = 'JAF-004';

-- -----------------------------------------------------------------------------
-- INSERTS, parents before children
-- -----------------------------------------------------------------------------

-- 7. Two first-time customers. Noor's first order is at the Rotterdam store, so she lives
--    in Rotterdam; Mateusz's is in Utrecht, so he lives there.
INSERT INTO sales.customers (id, name, street, postal_code, city, country) VALUES
    ('d0000000-0000-4000-8000-000000000001', 'Noor El Amrani',
     'Marsepeinkade 8', '3014 BT', 'Rotterdam', 'NL'),
    ('d0000000-0000-4000-8000-000000000002', 'Mateusz Lindqvist',
     'Kaneelsingel 215', '3521 HN', 'Utrecht', 'NL');

-- 8. Four orders.
--    - Rotterdam's first order, on its opening day (sales.stores.opened_at 2026-03-10), the
--      first order at any store but Utrecht: Nikos, at 07:42, early as his Utrecht orders
--      were, a for richer or pourover, 700, VAT at 0.09 = 63.
--    - Rotterdam's second, later that morning: Noor, a flame fandango at the new price and a
--      tangaroo, 1500 + 600 = 2100, VAT at 0.09 = 189.
--    - Mateusz's first order, in Utrecht: a hazelnut hotline, 1100, VAT at 0.09 = 99.
--    - Julia, under her new name, in Utrecht: a flame fandango and a chai and mighty,
--      1500 + 500 = 2000, VAT at 0.09 = 180.
INSERT INTO sales.orders (id, customer_id, store_id, ordered_at, subtotal, tax_paid, order_total) VALUES
    ('d0000000-0000-4000-8000-000000000104', '8224248d-b37a-4e73-8571-24b7d3435150',
     'c9471206-c14b-46c1-a93b-fe94ee386b24', '2026-03-10 07:42:00', 700, 63, 763),
    ('d0000000-0000-4000-8000-000000000101', 'd0000000-0000-4000-8000-000000000001',
     'c9471206-c14b-46c1-a93b-fe94ee386b24', '2026-03-10 11:05:00', 2100, 189, 2289),
    ('d0000000-0000-4000-8000-000000000102', 'd0000000-0000-4000-8000-000000000002',
     '2efff8c6-64e3-4e79-8257-c78ffd3395d0', '2025-11-29 09:12:00', 1100, 99, 1199),
    ('d0000000-0000-4000-8000-000000000103', '6350f2b4-88cb-4522-ab80-bce28ec10633',
     '2efff8c6-64e3-4e79-8257-c78ffd3395d0', '2025-11-29 12:40:00', 2000, 180, 2180);

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
     'Jaffles from the Jaffle Shop are the best! Ordered a flame fandango and a tangaroo.'),
    ('d0000000-0000-4000-8000-000000000302', '6350f2b4-88cb-4522-ab80-bce28ec10633',
     '2025-11-29 13:02:44.518207',
     'Jaffle Shop again. Ordered a flame fandango and a chai and mighty. This place is the worst.');

COMMIT;

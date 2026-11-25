-- Load the published Jaffle Shop corpus (jaffle/data/) into a clean relational model:
-- three business schemas, nine tables, a comment on every schema, table and column, and
-- nine validated foreign keys. Run it once, against an empty database, in ONE transaction:
--
--   psql -X -v ON_ERROR_STOP=1 --single-transaction -v datadir=/path/to/jaffle/data \
--        -f jaffle/db/load.sql
--
-- A failure anywhere rolls the whole load back, so the database never holds a partial
-- corpus that looks loaded. The file opens no transaction of its own: the caller supplies
-- one, with --single-transaction or a BEGIN around it, and the file refuses to run outside
-- one (see THE TRANSACTION GUARD). It turns psql's ON_ERROR_STOP on, and leaves it on, so
-- a failure stops psql even when the caller forgot the flag. In a container's
-- /docker-entrypoint-initdb.d, use initdb.sh beside this file: the postgres image runs a
-- .sql file there without a transaction, which the guard rejects.
--
-- Check the files against jaffle/SHA256SUMS before loading them; this file pins their
-- row counts, not their bytes.
--
-- THE DATA DIRECTORY
--
-- datadir is a psql variable naming the directory that holds the published CSV files. It
-- defaults to /data/jaffle, where a container mounts them.
--
-- The files are read with psql's client-side \copy, not the server-side COPY. \copy reads
-- the file where psql runs and streams it to the server, so the load works whether psql
-- and the server share a filesystem (psql inside the database container) or not (psql on a
-- CI runner or a laptop, talking to the server over the network), and it needs no
-- superuser or pg_read_server_files role. Server-side COPY would read the server's own
-- filesystem, which only the first case can arrange. psql does not expand variables in a
-- \copy line, so the load changes psql's working directory to datadir (\cd expands them)
-- and names each file relative to it. It changes back at the end: a caller that runs this
-- file with \ir, from a script it opened by a relative path, resolves its next \ir against
-- the working directory.
--
-- TYPES
--
-- Types come from profiling the files, not from defaults:
--   * ids that are UUIDs in every row are uuid; product skus and supply ids are text
--   * money is integer euro cents, exactly as the files write it (1100 = EUR 11.00)
--   * timestamps carry no offset in the files, so they are timestamp WITHOUT time zone;
--     adding a zone here would invent a fact the source never stated. tweeted_at has
--     microseconds; ordered_at and opened_at are whole seconds
--   * tax_rate is numeric(6,4). The scale is fixed, so a file value of 0.09 reads back as
--     0.0900, equal in value, not in text
--   * perishable is boolean; the files write True and False
--   * postal_code is text: a Dutch postal code holds a space and letters
-- Non-key columns stay nullable. The files hold no empty field at all, so the load
-- produces no NULL; that is a fact about these rows, not a contract a later insert has to
-- honor.
--
-- KEYS
--
-- The model is relational: every documented reference is a validated foreign key. Two
-- tables hold no rows and exist for their keys: catalog.supply_deliveries references a
-- supply by the two columns of its key, in one foreign key, and sales.store_transfers
-- references sales.stores twice, in two. A discovered contract records the first as one
-- relationship with two columns on each side, and the second as two relationships. Keys
-- are added after the rows are copied, so the database checks every loaded row against
-- them. No foreign key cascades: a later delete must remove children before parents, so
-- no source fact changes implicitly.
--
-- Every object is named with its schema, and the search path is pinned to pg_catalog
-- alone, so nothing resolves through search_path's "$user" or public entries: no schema
-- the caller happens to have can receive a table or shadow a type, function or operator
-- used here. SET LOCAL keeps the pin inside the transaction, so it does not leak into what
-- the caller runs after it.
--
-- THE TRANSACTION GUARD
--
-- The first statement records its transaction id in a transaction-local setting; the
-- second fails unless it runs in that same transaction. Under psql's autocommit each
-- statement is its own transaction, so the second sees another id and an empty setting,
-- and the load stops before it creates anything. Comparing transaction ids, not
-- timestamps, leaves nothing to clock resolution.
\set ON_ERROR_STOP on
SELECT pg_catalog.set_config('jaffle.load_xact', pg_catalog.pg_current_xact_id()::text, true)
    AS jaffle_load_xact \gset
DO $$
BEGIN
    IF pg_catalog.current_setting('jaffle.load_xact', true)
       IS DISTINCT FROM pg_catalog.pg_current_xact_id()::text THEN
        RAISE EXCEPTION 'jaffle load: load.sql must run inside one transaction'
            USING HINT = 'Run psql with --single-transaction (-1), or wrap the file in BEGIN and COMMIT.';
    END IF;
END
$$;

SET LOCAL search_path = pg_catalog;

\if :{?datadir}
\else
\set datadir /data/jaffle
\endif
\set jaffle_previous_dir `pwd`
\cd :datadir

-- Schemas: one per business domain.

CREATE SCHEMA sales;
COMMENT ON SCHEMA sales IS 'The shop''s ordering system: its customers, its stores, the orders customers place at them, and the items on each order.';

CREATE SCHEMA catalog;
COMMENT ON SCHEMA catalog IS 'Products and purchasing: the products the shop sells, and the supplies it buys to make them.';

CREATE SCHEMA marketing;
COMMENT ON SCHEMA marketing IS 'Social posts about the shop: the tweets customers post about their orders.';

-- Tables, in the column order of the model. COPY maps by explicit column lists below, so a
-- file's column order may differ from its table's.

CREATE TABLE sales.customers (
    id          uuid NOT NULL,
    name        text,
    street      text,
    postal_code text,
    city        text,
    country     text
);
COMMENT ON TABLE sales.customers IS 'One row per person who has placed one or more orders';
COMMENT ON COLUMN sales.customers.id IS 'Unique identifier for the customer.';
COMMENT ON COLUMN sales.customers.name IS 'Customer name as recorded by the source.';
COMMENT ON COLUMN sales.customers.street IS 'Street name and house number of the customer''s address. Synthetic: an invented street name with a generated house number, not a real address.';
COMMENT ON COLUMN sales.customers.postal_code IS 'Postal code of the customer''s address, in the Dutch format: four digits, a space and two letters. Synthetic: assigned independently of the street.';
COMMENT ON COLUMN sales.customers.city IS 'City of the customer''s address. Synthetic, like the rest of the address.';
COMMENT ON COLUMN sales.customers.country IS 'Country of the customer''s address, as an ISO 3166-1 alpha-2 code (NL). Synthetic, like the rest of the address.';

CREATE TABLE sales.stores (
    id        uuid NOT NULL,
    name      text,
    opened_at timestamp without time zone,
    tax_rate  numeric(6, 4)
);
COMMENT ON TABLE sales.stores IS 'The stores where orders are placed';
COMMENT ON COLUMN sales.stores.id IS 'Unique identifier for the store.';
COMMENT ON COLUMN sales.stores.name IS 'Store name as recorded by the source.';
COMMENT ON COLUMN sales.stores.opened_at IS 'Store opening timestamp, stored without a time zone. A date after the order window, the span of sales.orders.ordered_at, is a planned opening: that store has not traded yet.';
COMMENT ON COLUMN sales.stores.tax_rate IS 'VAT rate of the store, one flat rate for its whole menu, expressed as a decimal fraction; 0.09 represents 9%.';

CREATE TABLE sales.orders (
    id          uuid NOT NULL,
    customer_id uuid,
    store_id    uuid,
    ordered_at  timestamp without time zone,
    subtotal    integer,
    tax_paid    integer,
    order_total integer
);
COMMENT ON TABLE sales.orders IS 'One row per order; an order holds zero or more items';
COMMENT ON COLUMN sales.orders.id IS 'Unique identifier for the order.';
COMMENT ON COLUMN sales.orders.customer_id IS 'Customer who placed the order; references sales.customers.id.';
COMMENT ON COLUMN sales.orders.store_id IS 'Store where the order was placed; references sales.stores.id.';
COMMENT ON COLUMN sales.orders.ordered_at IS 'Order placement timestamp, stored without a time zone.';
COMMENT ON COLUMN sales.orders.subtotal IS 'Order subtotal excluding VAT, in integer euro cents.';
COMMENT ON COLUMN sales.orders.tax_paid IS 'VAT charged on the order, in integer euro cents: subtotal * the tax_rate of the store.';
COMMENT ON COLUMN sales.orders.order_total IS 'Total charged for the order including VAT, in integer euro cents: subtotal + tax_paid.';

CREATE TABLE sales.order_items (
    id       uuid NOT NULL,
    order_id uuid,
    sku      text
);
COMMENT ON TABLE sales.order_items IS 'One row per item in an order';
COMMENT ON COLUMN sales.order_items.id IS 'Unique identifier for the order item.';
COMMENT ON COLUMN sales.order_items.order_id IS 'Order containing this item; references sales.orders.id.';
COMMENT ON COLUMN sales.order_items.sku IS 'Product included in this order item; references catalog.products.sku.';

CREATE TABLE catalog.products (
    sku         text NOT NULL,
    name        text,
    type        text,
    price       integer,
    description text
);
COMMENT ON TABLE catalog.products IS 'One row per SKU sold in the stores';
COMMENT ON COLUMN catalog.products.sku IS 'Stock-keeping unit identifying the product.';
COMMENT ON COLUMN catalog.products.name IS 'Name of the product on the menu.';
COMMENT ON COLUMN catalog.products.type IS 'Product category recorded by the source.';
COMMENT ON COLUMN catalog.products.price IS 'Unit price of the product excluding VAT, in integer euro cents.';
COMMENT ON COLUMN catalog.products.description IS 'Source-provided description of the product.';

-- A supply is used by several products, so its id repeats: (id, sku) is the key.
CREATE TABLE catalog.supplies (
    id         text NOT NULL,
    sku        text NOT NULL,
    name       text,
    cost       integer,
    perishable boolean
);
COMMENT ON TABLE catalog.supplies IS 'One row per supply per SKU that uses it';
COMMENT ON COLUMN catalog.supplies.id IS 'Identifier for a supply; together with sku, uniquely identifies a supply-to-product row.';
COMMENT ON COLUMN catalog.supplies.sku IS 'Product SKU using the supply; references catalog.products.sku.';
COMMENT ON COLUMN catalog.supplies.name IS 'Name of the supply used by the product.';
COMMENT ON COLUMN catalog.supplies.cost IS 'Cost of the supply, in integer euro cents.';
COMMENT ON COLUMN catalog.supplies.perishable IS 'Whether the supply is marked perishable by the source.';

CREATE TABLE marketing.tweets (
    id          uuid NOT NULL,
    customer_id uuid,
    tweeted_at  timestamp without time zone,
    content     text
);
COMMENT ON TABLE marketing.tweets IS 'One row per tweet a customer posted about an order';
COMMENT ON COLUMN marketing.tweets.id IS 'Unique identifier for the tweet.';
COMMENT ON COLUMN marketing.tweets.customer_id IS 'Customer who posted the tweet; references sales.customers.id.';
COMMENT ON COLUMN marketing.tweets.tweeted_at IS 'Tweet posting timestamp, stored without a time zone.';
COMMENT ON COLUMN marketing.tweets.content IS 'Text of the tweet recorded by the source.';

-- A delivery names its supply by both columns of the supply's key. The foreign key lists
-- them in the key's order, supply_id then sku, which is not the order of the columns here.
CREATE TABLE catalog.supply_deliveries (
    id           uuid NOT NULL,
    sku          text,
    supply_id    text,
    delivered_at timestamp without time zone,
    quantity     integer
);
COMMENT ON TABLE catalog.supply_deliveries IS 'One row per delivery of a supply for one SKU; the corpus holds none';
COMMENT ON COLUMN catalog.supply_deliveries.id IS 'Unique identifier for the delivery.';
COMMENT ON COLUMN catalog.supply_deliveries.sku IS 'Product SKU the delivered supply is for; with supply_id, references catalog.supplies (id, sku).';
COMMENT ON COLUMN catalog.supply_deliveries.supply_id IS 'Supply delivered; with sku, references catalog.supplies (id, sku).';
COMMENT ON COLUMN catalog.supply_deliveries.delivered_at IS 'Delivery timestamp, stored without a time zone.';
COMMENT ON COLUMN catalog.supply_deliveries.quantity IS 'Number of units delivered.';

-- A transfer moves stock from one store to another, so it references sales.stores twice.
CREATE TABLE sales.store_transfers (
    id             uuid NOT NULL,
    from_store_id  uuid,
    to_store_id    uuid,
    transferred_at timestamp without time zone
);
COMMENT ON TABLE sales.store_transfers IS 'One row per transfer of stock between two stores; the corpus holds none';
COMMENT ON COLUMN sales.store_transfers.id IS 'Unique identifier for the transfer.';
COMMENT ON COLUMN sales.store_transfers.from_store_id IS 'Store the stock leaves; references sales.stores.id.';
COMMENT ON COLUMN sales.store_transfers.to_store_id IS 'Store the stock arrives at; references sales.stores.id.';
COMMENT ON COLUMN sales.store_transfers.transferred_at IS 'Transfer timestamp, stored without a time zone.';

-- Rows. HEADER MATCH fails the load when a file's header names differ from the column list,
-- so a column cannot land in the wrong place silently. Each column list is in the file's
-- order.

\copy sales.customers (id, name) FROM 'customers.csv' WITH (FORMAT csv, HEADER MATCH)
\copy sales.stores (id, name, opened_at, tax_rate) FROM 'stores.csv' WITH (FORMAT csv, HEADER MATCH)
\copy sales.orders (id, customer_id, ordered_at, store_id, subtotal, tax_paid, order_total) FROM 'orders.csv' WITH (FORMAT csv, HEADER MATCH)
\copy sales.order_items (id, order_id, sku) FROM 'order_items.csv' WITH (FORMAT csv, HEADER MATCH)
\copy catalog.products (sku, name, type, price, description) FROM 'products.csv' WITH (FORMAT csv, HEADER MATCH)
\copy catalog.supplies (id, name, cost, perishable, sku) FROM 'supplies.csv' WITH (FORMAT csv, HEADER MATCH)
\copy marketing.tweets (id, customer_id, tweeted_at, content) FROM 'tweets.csv' WITH (FORMAT csv, HEADER MATCH)

-- Primary keys, now that the rows are in.
ALTER TABLE sales.customers ADD CONSTRAINT customers_pkey PRIMARY KEY (id);
ALTER TABLE sales.stores ADD CONSTRAINT stores_pkey PRIMARY KEY (id);
ALTER TABLE sales.orders ADD CONSTRAINT orders_pkey PRIMARY KEY (id);
ALTER TABLE sales.order_items ADD CONSTRAINT order_items_pkey PRIMARY KEY (id);
ALTER TABLE catalog.products ADD CONSTRAINT products_pkey PRIMARY KEY (sku);
ALTER TABLE catalog.supplies ADD CONSTRAINT supplies_pkey PRIMARY KEY (id, sku);
ALTER TABLE marketing.tweets ADD CONSTRAINT tweets_pkey PRIMARY KEY (id);
ALTER TABLE catalog.supply_deliveries ADD CONSTRAINT supply_deliveries_pkey PRIMARY KEY (id);
ALTER TABLE sales.store_transfers ADD CONSTRAINT store_transfers_pkey PRIMARY KEY (id);

-- Addresses. customer_addresses.csv holds one address per customer; it is folded into
-- sales.customers and leaves no table behind. Its primary key rejects a second address for
-- one customer, which would otherwise make the UPDATE pick one of them arbitrarily.
CREATE TEMPORARY TABLE customer_addresses (
    customer_id uuid PRIMARY KEY,
    street      text,
    postal_code text,
    city        text,
    country     text
);
\copy pg_temp.customer_addresses (customer_id, street, postal_code, city, country) FROM 'customer_addresses.csv' WITH (FORMAT csv, HEADER MATCH)

UPDATE sales.customers AS c
   SET street = a.street,
       postal_code = a.postal_code,
       city = a.city,
       country = a.country
  FROM pg_temp.customer_addresses AS a
 WHERE a.customer_id = c.id;

-- Every customer has a complete address, every address belongs to a customer, and the
-- file holds its pinned number of rows.
DO $$
DECLARE
    addresses   bigint;
    unaddressed bigint;
    unmatched   bigint;
BEGIN
    SELECT count(*) INTO unaddressed
      FROM sales.customers
     WHERE street IS NULL OR postal_code IS NULL OR city IS NULL OR country IS NULL;
    IF unaddressed <> 0 THEN
        RAISE EXCEPTION 'jaffle load: % customer(s) have no complete address', unaddressed;
    END IF;

    SELECT count(*) INTO unmatched
      FROM pg_temp.customer_addresses AS a
     WHERE NOT EXISTS (SELECT FROM sales.customers AS c WHERE c.id = a.customer_id);
    IF unmatched <> 0 THEN
        RAISE EXCEPTION 'jaffle load: % address row(s) name no customer', unmatched;
    END IF;

    SELECT count(*) INTO addresses FROM pg_temp.customer_addresses;
    IF addresses <> 232 THEN
        RAISE EXCEPTION 'jaffle load: customer_addresses.csv holds % rows, the published corpus has 232',
            addresses;
    END IF;
END
$$;

DROP TABLE pg_temp.customer_addresses;

-- The nine references of the model, checked against every loaded row.
ALTER TABLE sales.orders ADD CONSTRAINT orders_customer_id_fkey
    FOREIGN KEY (customer_id) REFERENCES sales.customers (id);
ALTER TABLE sales.orders ADD CONSTRAINT orders_store_id_fkey
    FOREIGN KEY (store_id) REFERENCES sales.stores (id);
ALTER TABLE sales.order_items ADD CONSTRAINT order_items_order_id_fkey
    FOREIGN KEY (order_id) REFERENCES sales.orders (id);
ALTER TABLE sales.order_items ADD CONSTRAINT order_items_sku_fkey
    FOREIGN KEY (sku) REFERENCES catalog.products (sku);
ALTER TABLE catalog.supplies ADD CONSTRAINT supplies_sku_fkey
    FOREIGN KEY (sku) REFERENCES catalog.products (sku);
ALTER TABLE marketing.tweets ADD CONSTRAINT tweets_customer_id_fkey
    FOREIGN KEY (customer_id) REFERENCES sales.customers (id);
ALTER TABLE catalog.supply_deliveries ADD CONSTRAINT supply_deliveries_supply_fkey
    FOREIGN KEY (supply_id, sku) REFERENCES catalog.supplies (id, sku);
ALTER TABLE sales.store_transfers ADD CONSTRAINT store_transfers_from_store_id_fkey
    FOREIGN KEY (from_store_id) REFERENCES sales.stores (id);
ALTER TABLE sales.store_transfers ADD CONSTRAINT store_transfers_to_store_id_fkey
    FOREIGN KEY (to_store_id) REFERENCES sales.stores (id);

-- The digests pin the bytes; this pins what the loader made of them. The counts are the
-- data records of each published file (header excluded), counted with Python's csv module;
-- the two tables without a file hold none.
-- customer_addresses.csv's 232 records are counted with the address checks above.
DO $$
DECLARE
    expected record;
    actual   bigint;
BEGIN
    FOR expected IN
        SELECT * FROM (VALUES
            ('sales', 'customers', 232),
            ('sales', 'stores', 6),
            ('sales', 'orders', 6837),
            ('sales', 'order_items', 10025),
            ('catalog', 'products', 10),
            ('catalog', 'supplies', 65),
            ('marketing', 'tweets', 2161),
            ('catalog', 'supply_deliveries', 0),
            ('sales', 'store_transfers', 0)
        ) AS pin (sch, tbl, n)
    LOOP
        EXECUTE format('SELECT count(*) FROM ONLY %I.%I', expected.sch, expected.tbl)
            INTO actual;
        IF actual <> expected.n THEN
            RAISE EXCEPTION 'jaffle load: %.% holds % rows, the published corpus has %',
                expected.sch, expected.tbl, actual, expected.n;
        END IF;
    END LOOP;
END
$$;

ANALYZE sales.customers, sales.stores, sales.orders, sales.order_items,
        catalog.products, catalog.supplies, marketing.tweets,
        catalog.supply_deliveries, sales.store_transfers;

\cd :jaffle_previous_dir

-- The revert of mutation 3: the column added back as it was, nullable text with the column
-- comment db/load.sql gives it, which a harvest reads as its description. Expected: no change
-- against the held baseline.
ALTER TABLE catalog.products ADD COLUMN description text;
COMMENT ON COLUMN catalog.products.description IS 'Source-provided description of the product.';

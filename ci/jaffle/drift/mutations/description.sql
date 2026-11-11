-- Mutation 3: a column with no dependents dropped. Expected: column.dropped, GATE.
ALTER TABLE catalog.products DROP COLUMN description;

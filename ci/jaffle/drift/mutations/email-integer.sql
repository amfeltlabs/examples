-- The lag case's second change, after mutation 1: the new column retyped. Expected:
-- column.type.changed, REPAIR, against the stored contract.
ALTER TABLE sales.customers ALTER COLUMN email TYPE integer USING NULL::integer;

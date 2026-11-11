-- Mutation 1: a nullable column added. Expected: column.added.nullable, AUTO.
ALTER TABLE sales.customers ADD COLUMN email text;

-- Mutation 2: a decimal widened from numeric(6,4). Expected: column.type.widened, AUTO.
ALTER TABLE sales.stores ALTER COLUMN tax_rate TYPE numeric(8, 4);

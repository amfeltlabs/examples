-- Mutation 4: a table that a job names renamed. Expected: tweets table.dropped.with-dependents,
-- REPAIR, with candidate evidence medium; posts table.added.selected, AUTO.
ALTER TABLE marketing.tweets RENAME TO posts;

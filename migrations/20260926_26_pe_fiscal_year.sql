-- Existing immutable PE evidence has no proven fiscal year. Keep it NULL;
-- users must create and approve a revision rather than backfill old evidence.
ALTER TABLE pe_scenarios ADD COLUMN fiscal_year INTEGER
    CHECK (fiscal_year IS NULL OR (typeof(fiscal_year) = 'integer' AND fiscal_year BETWEEN 1900 AND 2200));

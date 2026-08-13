CREATE TRIGGER research_watchlist_symbol_canonical_insert
BEFORE INSERT ON research_watchlist_items
WHEN NOT (
    NEW.symbol GLOB '[0-9][0-9][0-9][0-9].TW'
    OR NEW.symbol GLOB '[0-9][0-9][0-9][0-9].TWO'
)
BEGIN
    SELECT RAISE(ABORT, 'research watchlist symbol must be canonical');
END;

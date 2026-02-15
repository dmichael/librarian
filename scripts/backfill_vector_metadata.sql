-- Backfill library and subjects from books table into pgvector chunk metadata.
-- Run with: psql -d librarian -f scripts/backfill_vector_metadata.sql

DO $$
DECLARE
    b RECORD;
    subjects_json TEXT;
    total_updated INT := 0;
    row_count INT;
    tbl TEXT;
BEGIN
    FOR b IN
        SELECT id, library, subjects
        FROM books
        WHERE status = 'indexed'
          AND (
            (library IS NOT NULL AND library != '')
            OR (subjects IS NOT NULL AND array_length(subjects, 1) > 0)
          )
    LOOP
        -- Convert postgres text[] to JSON array string
        SELECT COALESCE(array_to_json(b.subjects)::text, '[]') INTO subjects_json;

        FOREACH tbl IN ARRAY ARRAY['data_librarian_full', 'data_librarian_equations', 'data_librarian_chapters']
        LOOP
            EXECUTE format(
                'UPDATE %I SET metadata_ = jsonb_set(jsonb_set(metadata_, ''{library}'', %L::jsonb), ''{subjects}'', %L::jsonb) WHERE metadata_->>''book_id'' = %L',
                tbl,
                to_json(COALESCE(b.library, '')),
                subjects_json,
                b.id::text
            );
            GET DIAGNOSTICS row_count = ROW_COUNT;
            total_updated := total_updated + row_count;
        END LOOP;

        RAISE NOTICE 'Book %: library=%, subjects=%', b.id, b.library, subjects_json;
    END LOOP;

    RAISE NOTICE 'Total chunks updated: %', total_updated;
END $$;

BEGIN;

-- ============================================================================
-- 1. ADD CORRECT COUNT COLUMN
-- ============================================================================

ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS correct_count INTEGER NOT NULL DEFAULT 0;


-- ============================================================================
-- 2. ENSURE NON-NEGATIVE VALUES
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'questions_correct_count_check'
          AND conrelid = 'questions'::regclass
    ) THEN
        ALTER TABLE questions
            ADD CONSTRAINT questions_correct_count_check
            CHECK (correct_count >= 0);
    END IF;
END
$$;


-- ============================================================================
-- 3. RECALCULATE HISTORICAL CORRECT ANSWERS
-- ============================================================================
-- Recalculates from completed attempts.
-- Safe to execute multiple times.
-- Annulled and ungraded items are excluded.
-- Ownership must match, including legacy NULL owners.
-- Questions without correct answers receive zero.
-- ============================================================================

WITH historical_correct AS (
    SELECT
        i.question_id,
        COUNT(*)::INTEGER AS hits
    FROM attempt_items AS i
    JOIN attempts AS a
        ON a.id = i.attempt_id
    JOIN questions AS q
        ON q.id = i.question_id
    JOIN editions AS e
        ON e.id = q.edition_id
    WHERE a.status = 'completed'
      AND a.user_id IS NOT DISTINCT FROM e.user_id
      AND i.snapshot_annulled = 0
      AND i.snapshot_correct IS NOT NULL
      AND i.selected_answer = i.snapshot_correct
    GROUP BY i.question_id
)
UPDATE questions AS q
SET correct_count = COALESCE(h.hits, 0)
FROM (
    SELECT
        q2.id,
        hc.hits
    FROM questions AS q2
    LEFT JOIN historical_correct AS hc
        ON hc.question_id = q2.id
) AS h
WHERE q.id = h.id
  AND q.correct_count IS DISTINCT FROM COALESCE(h.hits, 0);


-- ============================================================================
-- 4. CREATE PERFORMANCE INDEX
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_questions_correct_count
    ON questions(correct_count);


COMMIT;
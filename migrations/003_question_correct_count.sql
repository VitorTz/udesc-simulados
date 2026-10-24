-- Track how many times a user has answered each of their questions correctly.
-- A correct answer counts only after the attempt is finalized. Annulled/ungraded
-- items never count; answer-key snapshots preserve the original grading.
-- Keep this migration immutable after it has been deployed.
ALTER TABLE questions
    ADD COLUMN correct_count INTEGER NOT NULL DEFAULT 0
    CHECK (correct_count >= 0);

-- Include past, already-completed attempts so existing users don't start at zero.
-- Legacy rows (both owners NULL) are included; migration 002 must run first.
WITH historical_correct AS (
    SELECT i.question_id, COUNT(*)::INTEGER AS hits
    FROM attempt_items i
    JOIN attempts a ON a.id = i.attempt_id
    JOIN questions q ON q.id = i.question_id
    JOIN editions e ON e.id = q.edition_id
    WHERE a.status = 'completed'
      AND a.user_id IS NOT DISTINCT FROM e.user_id
      AND i.snapshot_annulled = 0
      AND i.snapshot_correct IS NOT NULL
      AND i.selected_answer = i.snapshot_correct
    GROUP BY i.question_id
)
UPDATE questions AS q
SET correct_count = historical_correct.hits
FROM historical_correct
WHERE q.id = historical_correct.question_id;

CREATE INDEX idx_questions_correct_count ON questions (correct_count);

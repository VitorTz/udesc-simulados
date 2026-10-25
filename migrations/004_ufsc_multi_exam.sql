-- Add UFSC without changing existing UDESC historical questions or attempts.
-- Apply exactly once after 001..003; DO NOT edit after deployment.
ALTER TABLE editions ADD COLUMN institution TEXT NOT NULL DEFAULT 'UDESC'
    CHECK(institution IN ('UDESC','UFSC'));
ALTER TABLE editions ADD COLUMN booklet_color TEXT NOT NULL DEFAULT 'standard';
ALTER TABLE editions DROP CONSTRAINT IF EXISTS editions_label_key;
DROP INDEX IF EXISTS uq_editions_user_label;
CREATE UNIQUE INDEX uq_editions_owner_exam_color
    ON editions(user_id,institution,label,booklet_color);

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_role_check;
ALTER TABLE documents ADD CONSTRAINT documents_role_check CHECK (
    role IN ('morning','afternoon','key','key_morning','key_afternoon')
);

ALTER TABLE questions ADD COLUMN answer_kind TEXT NOT NULL DEFAULT 'choice'
    CHECK(answer_kind IN ('choice','sum','open'));
ALTER TABLE questions ADD COLUMN numeric_key INTEGER CHECK(numeric_key BETWEEN 0 AND 127);
ALTER TABLE questions ADD COLUMN proposition_count INTEGER CHECK(proposition_count BETWEEN 1 AND 7);

ALTER TABLE attempts ADD COLUMN institution TEXT NOT NULL DEFAULT 'UDESC'
    CHECK(institution IN ('UDESC','UFSC'));
ALTER TABLE attempts ADD COLUMN objective_points NUMERIC(9,2);
ALTER TABLE attempts ADD COLUMN objective_max NUMERIC(9,2);

ALTER TABLE attempt_items ADD COLUMN snapshot_kind TEXT NOT NULL DEFAULT 'choice'
    CHECK(snapshot_kind IN ('choice','sum','open'));
ALTER TABLE attempt_items ADD COLUMN snapshot_numeric INTEGER CHECK(snapshot_numeric BETWEEN 0 AND 127);
ALTER TABLE attempt_items ADD COLUMN snapshot_np INTEGER CHECK(snapshot_np BETWEEN 1 AND 7);
ALTER TABLE attempt_items ADD COLUMN selected_numeric INTEGER CHECK(selected_numeric BETWEEN 0 AND 127);

CREATE INDEX IF NOT EXISTS idx_editions_institution ON editions(user_id,institution);

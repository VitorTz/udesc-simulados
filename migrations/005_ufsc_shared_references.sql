-- UFSC: shared reference material independent of the question crop.
-- Keep original clips_json and pre-rendered question image caches unchanged.
ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS reference_clips_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE analysis_runs DROP CONSTRAINT IF EXISTS analysis_runs_kind_check;
ALTER TABLE analysis_runs ADD CONSTRAINT analysis_runs_kind_check
    CHECK (kind IN ('evidence', 'research', 'post_interview_review'));
INSERT INTO schema_migrations(version) VALUES (3) ON CONFLICT DO NOTHING;

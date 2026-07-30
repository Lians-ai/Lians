\set ON_ERROR_STOP on

-- Representative, non-production fixture for rehearsing migrations 0021-0028.
-- Every value in this file is synthetic. It is safe to include in a sanitized
-- staging dump, and it deliberately exercises legacy backfills and RLS changes.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM alembic_version
        WHERE version_num = '0020_decision_records'
    ) THEN
        RAISE EXCEPTION
            'Expected Alembic revision 0020_decision_records before seeding';
    END IF;
END
$$;

SELECT set_config('app.current_namespace', '__admin__', false);
SELECT set_config('agentmem.barrier_group', '', false);

INSERT INTO agents (agent_id, namespace, config)
VALUES
    ('staging-underwriter', 'staging-bank-a', '{"fixture": true}'::jsonb),
    ('staging-reviewer', 'staging-bank-b', '{"fixture": true}'::jsonb)
ON CONFLICT DO NOTHING;

INSERT INTO memories (
    id,
    namespace,
    agent_id,
    subject_id,
    metadata,
    event_time,
    ingestion_time,
    valid_from,
    valid_to,
    superseded_by,
    supersession_confidence,
    importance,
    source,
    content_hash
)
VALUES
    (
        '10000000-0000-4000-8000-000000000001',
        'staging-bank-a',
        'staging-underwriter',
        'subject-synthetic-001',
        '{"fixture": true, "field": "income"}'::jsonb,
        '2026-01-10T12:00:00Z',
        '2026-01-10T12:01:00Z',
        '2026-01-10T12:00:00Z',
        '2026-01-12T09:00:00Z',
        '10000000-0000-4000-8000-000000000002',
        0.99,
        0.8,
        'synthetic-credit-file-v1',
        repeat('1', 64)
    ),
    (
        '10000000-0000-4000-8000-000000000002',
        'staging-bank-a',
        'staging-underwriter',
        'subject-synthetic-001',
        '{"fixture": true, "field": "income", "restated": true}'::jsonb,
        '2026-01-12T09:00:00Z',
        '2026-01-12T09:01:00Z',
        '2026-01-12T09:00:00Z',
        NULL,
        NULL,
        NULL,
        0.9,
        'synthetic-credit-file-v2',
        repeat('2', 64)
    ),
    (
        '10000000-0000-4000-8000-000000000003',
        'staging-bank-b',
        'staging-reviewer',
        'subject-synthetic-002',
        '{"fixture": true, "field": "risk_rating"}'::jsonb,
        '2026-01-11T15:00:00Z',
        '2026-01-11T15:02:00Z',
        '2026-01-11T15:00:00Z',
        NULL,
        NULL,
        NULL,
        0.7,
        'synthetic-model-output',
        repeat('3', 64)
    )
ON CONFLICT DO NOTHING;

INSERT INTO conflict_flags (
    id,
    namespace,
    agent_id,
    memory_a_id,
    memory_b_id,
    confidence,
    status
)
VALUES (
    '20000000-0000-4000-8000-000000000001',
    'staging-bank-a',
    'staging-underwriter',
    '10000000-0000-4000-8000-000000000001',
    '10000000-0000-4000-8000-000000000002',
    0.97,
    'open'
)
ON CONFLICT DO NOTHING;

INSERT INTO pending_admissions (
    id,
    namespace,
    agent_id,
    content,
    event_time,
    source,
    subject_id,
    metadata,
    importance,
    risk_tags,
    reasons,
    status,
    created_at
)
VALUES (
    '30000000-0000-4000-8000-000000000001',
    'staging-bank-a',
    'staging-underwriter',
    'Synthetic staging-only admission record.',
    '2026-01-13T10:00:00Z',
    'synthetic-fixture',
    'subject-synthetic-003',
    '{"fixture": true}'::jsonb,
    0.6,
    '["regulated"]'::jsonb,
    '["human_review"]'::jsonb,
    'pending',
    '2026-01-13T10:00:01Z'
)
ON CONFLICT DO NOTHING;

INSERT INTO webhook_endpoints (
    id,
    namespace,
    url,
    secret,
    events,
    enabled,
    description
)
VALUES (
    '40000000-0000-4000-8000-000000000001',
    'staging-bank-a',
    'https://webhook.example.invalid/lians-staging',
    'synthetic-staging-secret-not-valid-anywhere',
    '["memory.superseded"]'::jsonb,
    true,
    'Synthetic endpoint used only for migration rehearsal.'
)
ON CONFLICT DO NOTHING;

INSERT INTO decision_records (
    id,
    namespace,
    agent_id,
    decision_type,
    outcome,
    reason_codes,
    regime,
    subject_id,
    session_id,
    model_id,
    model_version,
    policy_version,
    decided_at,
    recorded_at,
    knowledge_as_of,
    evidence_memory_ids,
    input_hash,
    output_hash,
    human_review_status,
    metadata,
    record_hash
)
VALUES
    (
        '50000000-0000-4000-8000-000000000001',
        'staging-bank-a',
        'staging-underwriter',
        'credit_limit_review',
        'manual_review',
        '["income_restatement"]'::json,
        'synthetic-regime-a',
        'subject-synthetic-001',
        'session-synthetic-001',
        'synthetic-model',
        '1.0',
        'policy-2026-01',
        '2026-01-12T10:00:00Z',
        '2026-01-12T10:00:02Z',
        '2026-01-12T09:30:00Z',
        '[
            "10000000-0000-4000-8000-000000000001",
            "10000000-0000-4000-8000-000000000002"
        ]'::json,
        repeat('a', 64),
        repeat('b', 64),
        'not_requested',
        '{"fixture": true, "scenario": "restated-source"}'::json,
        repeat('c', 64)
    ),
    (
        '50000000-0000-4000-8000-000000000002',
        'staging-bank-b',
        'staging-reviewer',
        'risk_rating_review',
        'approved',
        '["policy_passed"]'::json,
        'synthetic-regime-b',
        'subject-synthetic-002',
        'session-synthetic-002',
        'synthetic-model',
        '2.0',
        'policy-2026-02',
        '2026-01-11T16:00:00Z',
        '2026-01-11T16:00:03Z',
        '2026-01-11T15:30:00Z',
        '[
            "10000000-0000-4000-8000-000000000003",
            "not-a-uuid-and-intentionally-skipped"
        ]'::json,
        repeat('d', 64),
        repeat('e', 64),
        'completed',
        '{"fixture": true, "scenario": "invalid-evidence-id"}'::json,
        repeat('f', 64)
    )
ON CONFLICT DO NOTHING;

INSERT INTO ledger_events (
    id,
    namespace,
    event_type,
    agent_id,
    occurred_at,
    recorded_at,
    subject_id,
    session_id,
    decision_id,
    model_id,
    model_version,
    payload,
    artifact_hash,
    event_hash
)
VALUES (
    '60000000-0000-4000-8000-000000000001',
    'staging-bank-a',
    'decision.recorded',
    'staging-underwriter',
    '2026-01-12T10:00:00Z',
    '2026-01-12T10:00:02Z',
    'subject-synthetic-001',
    'session-synthetic-001',
    '50000000-0000-4000-8000-000000000001',
    'synthetic-model',
    '1.0',
    '{"fixture": true}'::json,
    repeat('9', 64),
    repeat('8', 64)
)
ON CONFLICT DO NOTHING;

SELECT
    (SELECT count(*) FROM memories) AS memories,
    (SELECT count(*) FROM decision_records) AS decision_records,
    (SELECT count(*) FROM ledger_events) AS ledger_events,
    (SELECT count(*) FROM pending_admissions) AS pending_admissions,
    (SELECT count(*) FROM webhook_endpoints) AS webhook_endpoints;

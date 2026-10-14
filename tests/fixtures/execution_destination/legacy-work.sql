-- Generated through the public workflow at Hormuz 07649907fc4243bc2578555fe06b8ff75fd2e753; synthetic metadata only.
BEGIN TRANSACTION;
CREATE TABLE ai_work_acquisition (
 acquisition_reference TEXT PRIMARY KEY, organization_id TEXT NOT NULL, actor_id TEXT NOT NULL,
 labels TEXT NOT NULL, consent_at REAL NOT NULL,
 UNIQUE(organization_id,actor_id)
);
CREATE TABLE ai_work_attempts (
 organization_id TEXT NOT NULL, actor_id TEXT NOT NULL, request_id TEXT NOT NULL,
 work_id TEXT NOT NULL REFERENCES ai_work_jobs(work_id), logical_request_id TEXT,
 retry_of TEXT, cache_source TEXT, model TEXT NOT NULL, protocol TEXT NOT NULL,
 route_fingerprint TEXT, request_pattern TEXT, request_shape TEXT NOT NULL DEFAULT 'plain', repeat_count INTEGER NOT NULL DEFAULT 0,
 pattern_excluded INTEGER NOT NULL DEFAULT 0,
 reason TEXT NOT NULL, state TEXT NOT NULL, reserved_microusd INTEGER NOT NULL,
 cost_microusd INTEGER, latency_ms REAL, created_at REAL NOT NULL,
 settled_at REAL, reservation_exceeded INTEGER NOT NULL DEFAULT 0, context_signature TEXT NOT NULL DEFAULT 'legacy', request_kind TEXT NOT NULL DEFAULT 'interactive', cache_bypass_reason TEXT, gateway_wall_ms REAL, gateway_overhead_ms REAL, timing_basis TEXT NOT NULL DEFAULT 'external_or_legacy_observation', timing_stages TEXT, confirmed_cost_microusd INTEGER, cost_confirmation_source TEXT, cost_confirmation_reference TEXT, cost_confirmed_at REAL, response_succeeded INTEGER NOT NULL DEFAULT 0, cache_generation INTEGER NOT NULL DEFAULT -1,
 PRIMARY KEY(organization_id, actor_id, request_id)
);
INSERT INTO "ai_work_attempts" VALUES('organization','developer','47bf7245-e7a3-4b50-956a-645378040173','work-a207f0f3f2914b4a8ce7da49b0f3894f','ebd65bfbb6b04b7b935a8ef4daff277c',NULL,NULL,'local-code','openai','b3dacf6bac52faa42f3348bb0456323dc1b71495e327a431e486c16e8c604182','231538d227a28525c5d159b563f567dc9aa60c24777017bd8221195bd8b84487','shape-v1:9c15d0454e71a766c6444f93bb64c4b804cb5d7a979d15ed7bb67e9f4c11fb7a.f7807d2fdf2aca9b4e2e7f3767e208dd',0,0,'benchmark_refresh_pending','succeeded',0,0,4.722920000000000451e-01,1.792016315587543965e+09,1.792016315592075109e+09,0,'f7807d2fdf2aca9b4e2e7f3767e208dd086789b21bb1cd1532fed0bf81b4fe07','interactive',NULL,19.277917,18.805625,'provider_transport_blocking_time','{"redaction":0.041584,"routing":6.232292}',NULL,NULL,NULL,NULL,1,0);
CREATE TABLE ai_work_bindings (
 binding_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, actor_id TEXT NOT NULL,
 work_id TEXT NOT NULL REFERENCES ai_work_jobs(work_id), provider TEXT NOT NULL,
 connector_id TEXT NOT NULL, object_digest TEXT NOT NULL, container_digest TEXT NOT NULL,
 completion_condition TEXT NOT NULL, created_at REAL NOT NULL, latest_event_at REAL,
 UNIQUE(organization_id,provider,connector_id,object_digest)
);
CREATE TABLE ai_work_bridge_events (
 event_digest TEXT PRIMARY KEY, delivery_digest TEXT NOT NULL,
 organization_id TEXT NOT NULL, connector_id TEXT NOT NULL, provider TEXT NOT NULL,
 object_digest TEXT NOT NULL, container_digest TEXT NOT NULL, event_type TEXT NOT NULL,
 object_type TEXT NOT NULL, quality_state TEXT NOT NULL, event_at REAL,
 status TEXT NOT NULL DEFAULT 'pending', reason TEXT NOT NULL DEFAULT 'pending',
 binding_id TEXT, created_at REAL NOT NULL
);
CREATE TABLE ai_work_deliveries (
 delivery_digest TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
 connector_id TEXT NOT NULL, provider TEXT NOT NULL, committed INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL
);
CREATE TABLE ai_work_exploration (
 organization_id TEXT NOT NULL, actor_id TEXT NOT NULL, work_id TEXT NOT NULL,
 observed_at REAL NOT NULL, model TEXT NOT NULL, reserved_microusd INTEGER NOT NULL,
 context_signature TEXT NOT NULL DEFAULT 'legacy',
 PRIMARY KEY(organization_id,actor_id,work_id)
);
CREATE TABLE ai_work_funnel (
 organization_id TEXT NOT NULL, actor_id TEXT NOT NULL, event TEXT NOT NULL,
 reference TEXT NOT NULL, occurred_at REAL NOT NULL,
 PRIMARY KEY(organization_id,actor_id,event,reference)
);
CREATE TABLE ai_work_jobs (
 work_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, actor_id TEXT NOT NULL,
 repository TEXT NOT NULL, title TEXT, task_type TEXT NOT NULL,
 context_revision TEXT, state TEXT NOT NULL, pause_reason TEXT,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL,
 cache_generation INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1,
 pinned_model TEXT
, task_origin TEXT NOT NULL DEFAULT 'declared', task_characteristics TEXT, context_signature TEXT NOT NULL DEFAULT 'legacy', completion_condition TEXT NOT NULL DEFAULT 'workflow.completed.v1');
INSERT INTO "ai_work_jobs" VALUES('work-a207f0f3f2914b4a8ce7da49b0f3894f','organization','developer','example/repository',NULL,'implementation','fixture-revision','active',NULL,1.792016315546544791e+09,1.792016315587543965e+09,NULL,0,1,'local-code','declared','{"version":"characteristics.v1","task_hint":"unknown","confidence":"unknown","input_size":"small","tools":false,"structured_output":false,"history":false}','f7807d2fdf2aca9b4e2e7f3767e208dd086789b21bb1cd1532fed0bf81b4fe07','workflow.completed.v1');
CREATE TABLE ai_work_observations (
 observation_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
 actor_id TEXT NOT NULL, work_id TEXT NOT NULL REFERENCES ai_work_jobs(work_id),
 status TEXT NOT NULL, source TEXT NOT NULL, reference TEXT, observed_at REAL NOT NULL
, completion_condition TEXT NOT NULL DEFAULT 'workflow.completed.v1');
CREATE TABLE ai_work_plans (
 organization_id TEXT NOT NULL, scope_type TEXT NOT NULL, scope_id TEXT NOT NULL,
 version INTEGER NOT NULL, budget_microusd INTEGER, objective TEXT NOT NULL,
 updated_at REAL NOT NULL, exploration_enabled INTEGER,
 PRIMARY KEY(organization_id, scope_type, scope_id)
);
CREATE TABLE ai_work_repeat_guards (
 organization_id TEXT NOT NULL, actor_id TEXT NOT NULL, work_id TEXT NOT NULL,
 request_pattern TEXT NOT NULL, context_signature TEXT NOT NULL,
 observed_at REAL NOT NULL, reason TEXT NOT NULL,
 last_attempt_rowid INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(organization_id,actor_id,work_id,request_pattern,context_signature)
);
CREATE TABLE ai_work_schema (version INTEGER PRIMARY KEY);
INSERT INTO "ai_work_schema" VALUES(1);
CREATE TABLE ai_work_workflow_keys (id INTEGER PRIMARY KEY CHECK(id=1), material BLOB NOT NULL);
INSERT INTO "ai_work_workflow_keys" VALUES(1,X'E6F50BCEF8949E9F3BB82CF2491DF0CAA75CBF0135E689491974B15D289FFE76');
CREATE INDEX ai_work_jobs_owner
 ON ai_work_jobs(organization_id, actor_id, created_at);
CREATE INDEX ai_work_jobs_profile
 ON ai_work_jobs(organization_id, repository, task_type, created_at);
CREATE INDEX ai_work_attempts_work ON ai_work_attempts(work_id, created_at);
CREATE INDEX ai_work_attempts_org_time ON ai_work_attempts(organization_id,created_at,work_id);
CREATE INDEX ai_work_observations_work
 ON ai_work_observations(work_id, observed_at);
CREATE UNIQUE INDEX ai_work_observation_replay
 ON ai_work_observations(organization_id, actor_id, work_id, status, source, reference)
 WHERE reference IS NOT NULL;
CREATE INDEX ai_work_exploration_org_time ON ai_work_exploration(organization_id,observed_at);
CREATE INDEX ai_work_bridge_delivery ON ai_work_bridge_events(delivery_digest,status);
COMMIT;

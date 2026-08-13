-- Claude Code Dashboard schema.
-- Everything derived from JSONL events + OTEL posts.

CREATE TABLE IF NOT EXISTS sessions (
  id                TEXT PRIMARY KEY,              -- session UUID
  project_hash      TEXT NOT NULL,                 -- parent dir under ~/.claude/projects/
  cwd               TEXT,
  model             TEXT,                          -- last seen model id on this session
  started_at        TEXT,                          -- ISO8601 of first event
  last_event_at     TEXT,                          -- ISO8601 of last processed event
  last_user_message TEXT,                          -- first 200 chars of latest user prompt
  last_modified_ts  REAL,                          -- max(file mtime) across all source files for this session
  entrypoint        TEXT,
  is_headless       INTEGER DEFAULT 0,
  version           TEXT,
  outcome           TEXT DEFAULT 'unfinished',     -- ok | errored | rate_limited | truncated | unfinished
  total_tool_errors INTEGER DEFAULT 0,
  event_count       INTEGER DEFAULT 0,
  provider          TEXT DEFAULT 'claude-code',    -- claude-code | codex | cursor | gemini
  cost_usd          REAL DEFAULT 0,               -- accumulated from messages.cost_usd
  activity_type     TEXT DEFAULT 'other',          -- classifier output
  one_shot          INTEGER DEFAULT 0,             -- edit succeeded without retry
  yielded           INTEGER,                       -- NULL=unanalyzed -1=not a git repo 0=no commit 1=productive commit 2=reverted
  yield_commit      TEXT                           -- first commit SHA in session window
);

-- Per-file ingestion cursor. One session may have multiple files
-- (main JSONL + subagent jsonls under <session-id>/subagents/).
CREATE TABLE IF NOT EXISTS file_cursors (
  source_file       TEXT PRIMARY KEY,
  session_id        TEXT NOT NULL,
  source_offset     INTEGER NOT NULL DEFAULT 0,
  last_modified_ts  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_file_cursors_session ON file_cursors(session_id);

CREATE INDEX IF NOT EXISTS idx_sessions_last_modified ON sessions(last_modified_ts);
CREATE INDEX IF NOT EXISTS idx_sessions_cwd ON sessions(cwd);
CREATE INDEX IF NOT EXISTS idx_sessions_model ON sessions(model);

CREATE TABLE IF NOT EXISTS messages (
  uuid                TEXT PRIMARY KEY,
  session_id          TEXT NOT NULL,
  parent_uuid         TEXT,
  role                TEXT NOT NULL,               -- user | assistant
  model               TEXT,
  ts                  TEXT NOT NULL,
  input_tokens        INTEGER DEFAULT 0,
  output_tokens       INTEGER DEFAULT 0,
  cache_read_tokens   INTEGER DEFAULT 0,
  cache_create_tokens INTEGER DEFAULT 0,
  cache_create_1h_tokens INTEGER DEFAULT 0,   -- 1h-TTL subset of cache_create_tokens; bills at 2x base input, not 1.25x
  cost_usd            REAL DEFAULT 0,
  text_preview        TEXT,
  agent_type          TEXT,                          -- subagent_type if this row came from a subagents/*.jsonl transcript, else NULL
  FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_agent_type ON messages(agent_type) WHERE agent_type IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_messages_model ON messages(model);

CREATE TABLE IF NOT EXISTS tool_calls (
  tool_use_id   TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL,
  message_uuid  TEXT,
  tool_name     TEXT NOT NULL,                     -- raw name (Read, Bash, mcp__server__tool, ...)
  is_mcp        INTEGER DEFAULT 0,
  mcp_server    TEXT,
  mcp_tool      TEXT,
  input_preview TEXT,
  started_at    TEXT,
  ended_at      TEXT,
  duration_ms   INTEGER,
  is_error      INTEGER,
  error_preview TEXT,
  FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_name ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_calls_started ON tool_calls(started_at);
CREATE INDEX IF NOT EXISTS idx_tool_calls_mcp ON tool_calls(is_mcp, mcp_server);

-- Skill invocations: detected when user prompts with a slash command.
CREATE TABLE IF NOT EXISTS skill_invocations (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id     TEXT NOT NULL,
  message_uuid   TEXT,
  skill_name     TEXT NOT NULL,
  ts             TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_skill_ts ON skill_invocations(ts);
CREATE INDEX IF NOT EXISTS idx_skill_name ON skill_invocations(skill_name);

-- OTEL raw stream. Keep the firehose so we can re-aggregate later.
CREATE TABLE IF NOT EXISTS otel_events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  kind       TEXT NOT NULL,                        -- log | metric
  event_name TEXT,
  session_id TEXT,
  ts         TEXT NOT NULL,
  attributes TEXT,                                 -- JSON blob
  received_at TEXT NOT NULL                        -- our receive timestamp
);

CREATE INDEX IF NOT EXISTS idx_otel_ts ON otel_events(ts);
CREATE INDEX IF NOT EXISTS idx_otel_event ON otel_events(event_name);
CREATE INDEX IF NOT EXISTS idx_otel_received ON otel_events(received_at);

-- Lightweight KV for ingest cursors, last-seen timestamps etc.
CREATE TABLE IF NOT EXISTS kv (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- System metrics: CPU, RAM, disk, load average.
CREATE TABLE IF NOT EXISTS system_metrics (
  timestamp         TEXT PRIMARY KEY,
  cpu_percent       REAL,
  memory_percent    REAL,
  memory_available_mb INTEGER,
  disk_percent      REAL,
  load_avg_1min     REAL,
  process_count     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_system_metrics_ts ON system_metrics(timestamp DESC);

-- Waste findings from optimize detector: superseded by `findings` below.

-- Hook execution status and counts.
CREATE TABLE IF NOT EXISTS hook_executions (
  timestamp    TEXT NOT NULL,
  hook_name    TEXT NOT NULL,
  status       TEXT NOT NULL,
  success_count INTEGER DEFAULT 0,
  error_count  INTEGER DEFAULT 0,
  last_ran_at  TEXT,
  PRIMARY KEY (timestamp, hook_name)
);
CREATE INDEX IF NOT EXISTS idx_hook_exec_recent ON hook_executions(hook_name, timestamp DESC);

-- =========================================================================
-- FIX QUEUE (v5)
--
-- One table for every actionable observation, replacing three parallel
-- systems that each had their own shape and none of which could be acted on:
--
--   waste_findings         8 detectors, 30-min background scan
--   intelligence()         6 live SQL rules, recomputed per request, unstored
--   recommendations.json   weekly Haiku call + deterministic drift engine
--
-- Producers write here; panels read from here. See ingest/findings.py for the
-- mapping from each producer's native shape.
-- =========================================================================

-- One row per distinct observation. UPSERTed by key on each scan, never
-- truncated: `waste_findings` was rewritten wholesale every 30 minutes, which
-- is what made state, history and verification impossible to add.
CREATE TABLE IF NOT EXISTS findings (
  key           TEXT PRIMARY KEY,                  -- stable across scans; see KEY FORMAT below
  source        TEXT NOT NULL,                     -- detector | signal | drift | llm
  gauge         TEXT NOT NULL,                     -- burn | discipline | efficiency | hygiene
  severity      TEXT NOT NULL,                     -- high | medium | low | ok
  title         TEXT NOT NULL,
  detail        TEXT,

  -- NULL and 0 mean different things and must not be conflated. NULL = this
  -- finding's cost is not derivable from the data we hold (bash_bloat has no
  -- result-size column to read; low_read_edit_ratio's real cost is retries,
  -- which nothing records). 0 = measured, and genuinely nothing. Five of the
  -- eight detectors were shipping fabricated integers here before the audit,
  -- and a made-up number outranks every honest one the moment the queue sorts
  -- by cost. Rank on (severity, est_tokens DESC NULLS LAST).
  est_tokens    INTEGER,
  est_usd       REAL,

  evidence_sql  TEXT,                              -- runnable; the operator's audit trail
  fix_kind      TEXT NOT NULL DEFAULT 'manual',    -- shell | prompt | manual
  fix_label     TEXT,
  fix_text      TEXT,

  -- Hard gate on the short leash, enforced here rather than by convention in
  -- calling code. Only a hardcoded shell one-liner from detector source may
  -- ever execute unattended: a hallucinated command is destructive whether or
  -- not anyone meant harm, so LLM-authored text is structurally barred from
  -- the auto-run path.
  auto_run      INTEGER NOT NULL DEFAULT 0 CHECK (
                  auto_run = 0 OR (source = 'detector' AND fix_kind = 'shell')),

  first_seen    TEXT NOT NULL,
  last_seen     TEXT NOT NULL,
  last_scan     TEXT NOT NULL,                     -- scan id that most recently saw it
  resolved_at   TEXT,                              -- set when a scan stops seeing it

  CHECK (source   IN ('detector','signal','drift','llm')),
  CHECK (gauge    IN ('burn','discipline','efficiency','hygiene')),
  CHECK (severity IN ('high','medium','low','ok')),
  CHECK (fix_kind IN ('shell','prompt','manual'))
);
CREATE INDEX IF NOT EXISTS idx_findings_open ON findings(resolved_at, severity, est_tokens DESC);
CREATE INDEX IF NOT EXISTS idx_findings_gauge ON findings(gauge, resolved_at);

-- KEY FORMAT — "<source>:<slug>", stable across scans so state survives:
--   detector:duplicate_reads          detector pattern name
--   signal:error-rate-elevated        the id intelligence() already emits
--   drift:opus_cost_share             the CLAUDE.md rule id
--   llm:<sha1(title)[:12]>            unstable by nature — Haiku rewords the
--                                     same observation, so state on an llm row
--                                     is best-effort and it never auto-runs

-- Operator's verdict on a finding. Separate table because findings are
-- rewritten by the scanner and a decision must outlive that.
CREATE TABLE IF NOT EXISTS finding_state (
  key           TEXT PRIMARY KEY,
  status        TEXT NOT NULL,                     -- open | snoozed | accepted | fixed
  note          TEXT,
  snooze_until  TEXT,                              -- ISO8601; only for status='snoozed'
  changed_at    TEXT NOT NULL,
  CHECK (status IN ('open','snoozed','accepted','fixed'))
);

-- 'accepted' is the important one: it means "this is deliberate, stop counting
-- it". 835 unused skills is a real measurement and a decision already made —
-- without this the grade stays hostage to a choice the operator won't reverse.
-- Grade counts findings that are unresolved, not accepted, not snoozed, and
-- not severity='ok' (intelligence() emits 'ok' rows as positive confirmation,
-- which are worth showing and must not be scored as failures).

-- Every mutation the dashboard performs, with the command that reverses it.
-- The undo log is a usability feature, not a security control: the point is
-- that a fix can always be put back without reconstructing what it did.
CREATE TABLE IF NOT EXISTS fix_actions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  finding_key   TEXT NOT NULL,
  fix_kind      TEXT NOT NULL,
  command       TEXT NOT NULL,                     -- what ran, verbatim
  undo_command  TEXT,                              -- NULL = not reversible; UI must say so
  status        TEXT NOT NULL,                     -- ok | failed | undone
  output        TEXT,                              -- truncated stdout+stderr
  exit_code     INTEGER,
  ran_at        TEXT NOT NULL,
  undone_at     TEXT,
  CHECK (status IN ('ok','failed','undone'))
);
CREATE INDEX IF NOT EXISTS idx_fix_actions_recent ON fix_actions(ran_at DESC);

-- Anthropic plan-usage samples. /plan-usage passes the API's numbers straight
-- through with a 60s cache and keeps nothing, so there is no series to project
-- from — "on pace to hit the weekly cap Thursday" is not computable today.
-- Sampled on the existing poll.
CREATE TABLE IF NOT EXISTS plan_usage_samples (
  ts            TEXT NOT NULL,
  kind          TEXT NOT NULL,                     -- session | weekly_all | weekly_scoped
  scope_label   TEXT,                              -- e.g. 'Fable' on weekly_scoped
  percent       REAL NOT NULL,
  resets_at     TEXT,
  PRIMARY KEY (ts, kind, scope_label)
);
CREATE INDEX IF NOT EXISTS idx_plan_usage_kind ON plan_usage_samples(kind, ts DESC);

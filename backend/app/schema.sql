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
  yielded           INTEGER,                       -- NULL=unknown 0=no commit 1=committed
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

-- Waste findings from optimize detector.
CREATE TABLE IF NOT EXISTS waste_findings (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT,
  pattern     TEXT NOT NULL,
  severity    TEXT NOT NULL,                       -- high | medium | low
  detail      TEXT,
  tokens_saved INTEGER DEFAULT 0,
  cost_impact REAL DEFAULT 0,
  fix_label   TEXT,
  fix_text    TEXT,
  detected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_waste_pattern ON waste_findings(pattern, detected_at DESC);

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

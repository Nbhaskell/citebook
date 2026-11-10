-- 001_init: the tables in docs/BACKEND_SCHEMA.md section 3, copied exactly, then the indexes.
-- bookings(reference) and messages(conversation_id, seq) are UNIQUE, so SQLite already indexes them.

CREATE TABLE schema_migrations (
  version     INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  applied_at  TEXT NOT NULL
);

CREATE TABLE settings (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL            -- JSON
);
-- keys: business_tz, office_hours, visit_hours, closed_dates,
-- arrival_window_minutes, hold_ttl_business_hours, staff_confirm_typical_business_hours, quote_ttl_minutes,
-- free_cancel_hours, max_reschedules, zip_areas, verification_max_attempts,
-- synthetic_data, provider, model_name
-- office_hours, visit_hours and closed_dates come from universe/facts.json.
-- zip_areas: {"<zip>": {"area": "<neighbourhood>", "zone": "core"|"outer"}} from
-- universe/citebook_extension.json. Prices and fees live in db/seed/price_book.json.

CREATE TABLE services (
  code                 TEXT PRIMARY KEY,
  name                 TEXT NOT NULL,
  description          TEXT NOT NULL,
  skill                TEXT NOT NULL,                                 -- universe service_type a technician must have
  windows_needed       INTEGER NOT NULL CHECK (windows_needed IN (1,2)),  -- consecutive arrival windows one visit uses
  pricing_kind         TEXT NOT NULL CHECK (pricing_kind IN ('fixed','starting_price','hourly','quote_only')),
  price_minor          INTEGER CHECK (price_minor >= 0),              -- job price for fixed / starting_price; NULL otherwise
  min_hours            TEXT,                                          -- Decimal string; hourly only
  bookable_online      INTEGER NOT NULL CHECK (bookable_online IN (0,1)),   -- 0 for quote_only and for jobs over the written-estimate limit
  first_step_code      TEXT REFERENCES services(code),               -- quote_only: the bookable first step, if any
  active               INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  CHECK (pricing_kind <> 'quote_only' OR bookable_online = 0),
  CHECK ((pricing_kind IN ('fixed','starting_price')) = (price_minor IS NOT NULL))
);
-- Call-out fee, emergency call-out fee, labour rate, outer-area travel fee and the
-- call-out credit rule come from db/seed/price_book.json (values from universe/facts.json).

CREATE TABLE technicians (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,            -- invented
  skills      TEXT NOT NULL,            -- JSON array of universe service_type values
  active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

CREATE TABLE slots (
  id             TEXT PRIMARY KEY,
  technician_id  TEXT NOT NULL REFERENCES technicians(id),
  starts_at      TEXT NOT NULL,                    -- start of one arrival window
  ends_at        TEXT NOT NULL,                    -- starts_at + arrival_window_minutes (120)
  status         TEXT NOT NULL CHECK (status IN ('open','held','booked','blocked')),
  version        INTEGER NOT NULL DEFAULT 0,       -- optimistic locking
  UNIQUE (technician_id, starts_at)
);

CREATE TABLE customers (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  phone           TEXT NOT NULL,
  email           TEXT,
  address         TEXT NOT NULL,
  zip_code        TEXT NOT NULL,
  created_at      TEXT NOT NULL
);

CREATE TABLE quotes (
  id                 TEXT PRIMARY KEY,
  conversation_id    TEXT REFERENCES conversations(id),   -- NULL only for quotes created by the seed
  service_code       TEXT NOT NULL REFERENCES services(code),
  slot_id            TEXT REFERENCES slots(id),    -- required before a quote can back a hold or reschedule
  zip_code           TEXT NOT NULL,                -- zone decides the travel-fee line
  lines              TEXT NOT NULL,                -- JSON: [{"label","amount_minor"}]; a credit line is negative
  total_minor        INTEGER NOT NULL,
  is_estimate        INTEGER NOT NULL CHECK (is_estimate IN (0,1)),
  estimate_note      TEXT,                         -- fixed copy from code, e.g. "Final price confirmed on site"
  price_book_version TEXT NOT NULL,
  created_at         TEXT NOT NULL,
  expires_at         TEXT NOT NULL
);

CREATE TABLE bookings (
  id                 TEXT PRIMARY KEY,
  reference          TEXT NOT NULL UNIQUE,         -- CB-XXXXXX
  customer_id        TEXT NOT NULL REFERENCES customers(id),
  service_code       TEXT NOT NULL REFERENCES services(code),
  slot_id            TEXT NOT NULL REFERENCES slots(id),   -- first arrival window; a 2-window visit also holds the next one
  quote_id           TEXT NOT NULL REFERENCES quotes(id),  -- replaced by the new quote on reschedule
  status             TEXT NOT NULL CHECK (status IN ('held','confirmed','cancelled','completed','no_show','expired')),
  reschedule_count   INTEGER NOT NULL DEFAULT 0,
  hold_expires_at    TEXT,                         -- counted in office hours (hold_ttl_business_hours)
  customer_notes     TEXT,                         -- seeded only (no tool writes it); untrusted; never shown to the model unmarked
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);

CREATE TABLE conversations (
  id                   TEXT PRIMARY KEY,
  channel              TEXT NOT NULL CHECK (channel IN ('web_widget','simulator')),
  status               TEXT NOT NULL CHECK (status IN ('active','awaiting_confirmation','handed_off','closed','abandoned')),
  verification_state   TEXT NOT NULL CHECK (verification_state IN ('anonymous','contact_collected','verified')),
  customer_id          TEXT REFERENCES customers(id),     -- set once contact collected or verified
  verified_booking_id  TEXT REFERENCES bookings(id),
  verification_failures INTEGER NOT NULL DEFAULT 0,
  scenario_id          TEXT,                              -- simulator runs only
  started_at           TEXT NOT NULL,
  last_activity_at     TEXT NOT NULL,
  closed_at            TEXT
);

CREATE TABLE messages (
  id               TEXT PRIMARY KEY,
  conversation_id  TEXT NOT NULL REFERENCES conversations(id),
  seq              INTEGER NOT NULL,
  role             TEXT NOT NULL CHECK (role IN ('customer','assistant','tool','system_note','staff')),
  content          TEXT NOT NULL,          -- masked for sensitive numbers before storage
  created_at       TEXT NOT NULL,
  UNIQUE (conversation_id, seq)
);

CREATE TABLE screen_flags (
  id               TEXT PRIMARY KEY,
  message_id       TEXT NOT NULL REFERENCES messages(id),
  flag             TEXT NOT NULL CHECK (flag IN ('EMERGENCY','SUSPICIOUS_INPUT','SENSITIVE_NUMBER','ABUSIVE','SPAM')),
  detail           TEXT NOT NULL           -- JSON; pattern ids only, no message text
);

CREATE TABLE tool_calls (
  id               TEXT PRIMARY KEY,
  conversation_id  TEXT NOT NULL REFERENCES conversations(id),
  turn             INTEGER NOT NULL,
  tool             TEXT NOT NULL,          -- may be an unknown name proposed by the model
  arguments        TEXT NOT NULL,          -- JSON as proposed by the model
  gate_decision    TEXT NOT NULL CHECK (gate_decision IN ('ALLOWED','NOT_ALLOWED_IN_STATE','NEEDS_VERIFICATION','NEEDS_CONTACT','INVALID_ARGUMENTS','UNKNOWN_TOOL','RATE_LIMITED','POLICY_BLOCKED','STAFF_ONLY')),
  gate_detail      TEXT,                   -- JSON
  result           TEXT,                   -- JSON returned to the model (only when ALLOWED)
  duration_ms      INTEGER,
  created_at       TEXT NOT NULL
);

CREATE TABLE actions (
  id                  TEXT PRIMARY KEY,
  conversation_id     TEXT NOT NULL REFERENCES conversations(id),
  origin              TEXT NOT NULL CHECK (origin IN ('tool','screening','loop')),
  tool_call_id        TEXT REFERENCES tool_calls(id),     -- set only when origin = 'tool'
  action_type         TEXT NOT NULL CHECK (action_type IN ('hold_create','reschedule','cancellation','refund_request','policy_exception','handoff')),
  status              TEXT NOT NULL CHECK (status IN ('proposed','customer_confirmed','pending_staff','approved','rejected','executed','expired','withdrawn','failed')),
  booking_id          TEXT REFERENCES bookings(id),
  payload             TEXT NOT NULL,       -- JSON: everything needed to execute, built by code
  summary             TEXT NOT NULL,       -- fixed-template text built by code from payload
  payload_hash        TEXT NOT NULL,       -- SHA-256 of canonical payload; the confirmation binds to it
  escalation_reason   TEXT CHECK (escalation_reason IN ('CUSTOMER_ASKED_FOR_PERSON','EMERGENCY','REFUND','LATE_CANCELLATION','LATE_RESCHEDULE','POLICY_EXCEPTION','OUT_OF_SERVICE_AREA','NOT_IN_KNOWLEDGE_BASE','VERIFICATION_FAILED','REPEATED_GATE_BLOCKS','REPLY_CHECK_FAILED','SUSPICIOUS_INPUT','MODEL_UNAVAILABLE')),
  expires_at          TEXT,
  customer_confirmed_at TEXT,
  executed_at         TEXT,
  failure_detail      TEXT,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL,
  CHECK ((origin = 'tool') = (tool_call_id IS NOT NULL))
);

CREATE TABLE staff_decisions (
  id             TEXT PRIMARY KEY,
  action_id      TEXT REFERENCES actions(id),
  conversation_id TEXT REFERENCES conversations(id),
  decision       TEXT NOT NULL CHECK (decision IN ('approve','reject','take_over','close')),
  reason         TEXT,                     -- required for reject
  decided_at     TEXT NOT NULL,
  review_seconds INTEGER
);

CREATE TABLE reply_checks (
  id               TEXT PRIMARY KEY,
  message_id       TEXT NOT NULL REFERENCES messages(id),
  attempt          INTEGER NOT NULL,       -- 1 = first draft, 2 = regenerated
  check_code       TEXT NOT NULL CHECK (check_code IN ('PRICE_FROM_TOOL','TIME_FROM_TOOL','REFERENCE_FROM_TOOL','POLICY_GROUNDED','NO_FALSE_CONFIRMATION','NO_OTHER_CUSTOMER_DATA','NO_PROMISED_REFUND','SCOPE')),
  passed           INTEGER NOT NULL CHECK (passed IN (0,1)),
  detail           TEXT NOT NULL           -- JSON; values found and what they were compared with
);

CREATE TABLE outbox (                        -- notification DRAFTS only; nothing is sent
  id               TEXT PRIMARY KEY,
  booking_id       TEXT REFERENCES bookings(id),
  kind             TEXT NOT NULL CHECK (kind IN ('hold_received','booking_confirmed','hold_declined','hold_expired','booking_rescheduled','booking_cancelled','request_received')),
  body             TEXT NOT NULL,          -- fixed template filled by code
  created_at       TEXT NOT NULL
);

CREATE TABLE audit_events (
  id               TEXT PRIMARY KEY,
  at               TEXT NOT NULL,
  actor            TEXT NOT NULL CHECK (actor IN ('assistant','customer','staff','system','simulator')),
  event            TEXT NOT NULL,          -- e.g. tool_gated, action_proposed, action_confirmed, action_executed, staff_decided
  conversation_id  TEXT,
  action_id        TEXT,
  payload          TEXT NOT NULL           -- JSON; ids, codes, timings only
);

CREATE INDEX idx_slots_status_starts ON slots(status, starts_at);
CREATE INDEX idx_bookings_customer ON bookings(customer_id);
CREATE INDEX idx_actions_status ON actions(status);
CREATE INDEX idx_tool_calls_conversation_turn ON tool_calls(conversation_id, turn);

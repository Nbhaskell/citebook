-- A conversation's session token is stored only as a SHA-256 hash (TRD section 13).
ALTER TABLE conversations ADD COLUMN session_token_hash TEXT;
CREATE UNIQUE INDEX idx_conversations_session_token ON conversations(session_token_hash);

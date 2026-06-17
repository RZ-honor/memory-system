"""SQLite database with FTS5 full-text search, vector support, and FSRS-based temporal memory."""
import sqlite3, json, time, hashlib, uuid
from lib import config, logger

_log = logger.get()

_CONN = None

# Whitelists for SQL field names to prevent injection via dynamic UPDATE queries
_MEMORY_FIELDS = frozenset({
    "project", "category", "obs_type", "memory_type", "title", "subtitle",
    "narrative", "facts", "concepts", "files_read", "files_modified", "name",
    "description", "content", "origin_session", "generated_by", "content_hash",
    "embedding", "created_at", "updated_at", "last_accessed_at", "access_count",
    "importance", "stability", "supersedes", "session_phase", "is_active",
    "relevance_count", "merged_into", "metadata", "module_id", "related_to",
})
_MODULE_FIELDS = frozenset({
    "project", "name", "description", "memory_count", "embedding",
    "created_at", "updated_at",
})
_SKILL_FIELDS = frozenset({
    "project", "name", "description", "workflow", "trigger_keywords",
    "stop_conditions", "output_format", "examples", "gotchas",
    "references_list", "confidence", "use_count", "last_used_at",
    "origin_session", "is_active", "created_at", "updated_at",
})
_REASONING_FIELDS = frozenset({
    "project", "module_id", "session_uuid", "thinking_mode", "question",
    "steps", "outcome", "outcome_summary", "failure_reason",
    "extracted_facts", "embedding", "importance", "is_active",
    "created_at", "updated_at", "last_accessed_at", "access_count",
})


def _validate_fields(fields, whitelist, label):
    """Validate that all field names are in the whitelist. Raises ValueError on invalid names."""
    invalid = [k for k in fields if k not in whitelist]
    if invalid:
        raise ValueError(f"Invalid {label} fields: {invalid}")


def connect():
    global _CONN
    if _CONN is not None:
        return _CONN
    cfg = config.load()
    _CONN = sqlite3.connect(cfg["db_path"], check_same_thread=False)
    _CONN.row_factory = sqlite3.Row
    _CONN.execute("PRAGMA journal_mode=WAL")
    _CONN.execute("PRAGMA foreign_keys=ON")
    _init_schema(_CONN)
    return _CONN

def _init_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS memories (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        project         TEXT NOT NULL,
        category        TEXT NOT NULL DEFAULT 'observation',
        obs_type        TEXT,
        memory_type     TEXT NOT NULL DEFAULT 'episodic',
        title           TEXT,
        subtitle        TEXT,
        narrative       TEXT,
        facts           TEXT DEFAULT '[]',
        concepts        TEXT DEFAULT '[]',
        files_read      TEXT DEFAULT '[]',
        files_modified  TEXT DEFAULT '[]',
        name            TEXT,
        description     TEXT,
        content         TEXT,
        origin_session  TEXT,
        generated_by    TEXT,
        content_hash    TEXT,
        embedding       BLOB,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        last_accessed_at TEXT,
        access_count    INTEGER DEFAULT 0,
        importance      INTEGER DEFAULT 5,
        stability       REAL DEFAULT 1.0,
        supersedes      INTEGER,
        session_phase   INTEGER,
        is_active       INTEGER NOT NULL DEFAULT 1,
        relevance_count INTEGER DEFAULT 0,
        merged_into     INTEGER,
        metadata        TEXT DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        session_uuid    TEXT UNIQUE NOT NULL,
        project         TEXT NOT NULL,
        user_prompt     TEXT,
        started_at      TEXT NOT NULL,
        completed_at    TEXT,
        status          TEXT DEFAULT 'active',
        summary         TEXT,
        tool_count      INTEGER DEFAULT 0,
        msg_count       INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS pending_queue (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        session_uuid    TEXT,
        project         TEXT NOT NULL,
        hook_event      TEXT NOT NULL,
        tool_name       TEXT,
        tool_input      TEXT,
        tool_response   TEXT,
        cwd             TEXT,
        extra           TEXT DEFAULT '{}',
        status          TEXT DEFAULT 'pending',
        retry_count     INTEGER DEFAULT 0,
        last_error      TEXT,
        created_at      TEXT NOT NULL,
        processed_at    TEXT
    );

    CREATE TABLE IF NOT EXISTS fusion_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        action          TEXT NOT NULL,
        source_id       INTEGER,
        target_id       INTEGER,
        reason          TEXT,
        created_at      TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS knowledge_index (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        project         TEXT NOT NULL,
        key             TEXT NOT NULL,
        value           TEXT,
        memory_id       INTEGER,
        updated_at      TEXT NOT NULL,
        UNIQUE(project, key)
    );

    CREATE TABLE IF NOT EXISTS skills (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        project         TEXT NOT NULL,
        name            TEXT NOT NULL,
        description     TEXT,
        workflow        TEXT DEFAULT '[]',
        trigger_keywords TEXT DEFAULT '[]',
        stop_conditions TEXT DEFAULT '[]',
        output_format   TEXT,
        examples        TEXT DEFAULT '[]',
        gotchas         TEXT DEFAULT '[]',
        references_list TEXT DEFAULT '[]',
        confidence      INTEGER DEFAULT 3,
        use_count       INTEGER DEFAULT 0,
        last_used_at    TEXT,
        origin_session  TEXT,
        is_active       INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        UNIQUE(project, name)
    );

    CREATE TABLE IF NOT EXISTS memory_access_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        memory_id       INTEGER NOT NULL,
        query           TEXT,
        score           REAL,
        accessed_at     TEXT NOT NULL,
        FOREIGN KEY(memory_id) REFERENCES memories(id)
    );

    CREATE TABLE IF NOT EXISTS memory_modules (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        project         TEXT NOT NULL,
        name            TEXT NOT NULL,
        description     TEXT,
        memory_count    INTEGER DEFAULT 0,
        embedding       BLOB,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        UNIQUE(project, name)
    );

    CREATE TABLE IF NOT EXISTS reasoning_chains (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        project         TEXT NOT NULL,
        module_id       INTEGER,
        session_uuid    TEXT,
        thinking_mode   TEXT NOT NULL DEFAULT 'cot',
        question        TEXT,
        steps           TEXT DEFAULT '[]',
        outcome         TEXT DEFAULT 'pending',
        outcome_summary TEXT,
        failure_reason  TEXT,
        extracted_facts TEXT DEFAULT '[]',
        embedding       BLOB,
        importance      INTEGER DEFAULT 5,
        is_active       INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        last_accessed_at TEXT,
        access_count    INTEGER DEFAULT 0,
        FOREIGN KEY (module_id) REFERENCES memory_modules(id)
    );
    """)

    # Add module_id and related_to columns to memories (for existing DBs)
    for alter_sql in [
        "ALTER TABLE memories ADD COLUMN module_id INTEGER",
        "ALTER TABLE memories ADD COLUMN related_to TEXT DEFAULT '[]'",
        "ALTER TABLE reasoning_chains ADD COLUMN module_id INTEGER",
    ]:
        try:
            conn.execute(alter_sql)
        except sqlite3.OperationalError:
            pass  # Column already exists

    # Indexes for modules
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_mem_module ON memories(module_id, is_active)",
        "CREATE INDEX IF NOT EXISTS idx_modules_project ON memory_modules(project)",
        "CREATE INDEX IF NOT EXISTS idx_rc_project ON reasoning_chains(project, is_active)",
        "CREATE INDEX IF NOT EXISTS idx_rc_session ON reasoning_chains(session_uuid)",
        "CREATE INDEX IF NOT EXISTS idx_rc_mode ON reasoning_chains(thinking_mode, is_active)",
        "CREATE INDEX IF NOT EXISTS idx_rc_outcome ON reasoning_chains(outcome, is_active)",
        "CREATE INDEX IF NOT EXISTS idx_rc_module ON reasoning_chains(module_id, is_active)",
    ]:
        conn.execute(idx_sql)

    # FTS5 virtual tables
    try:
        conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            title, subtitle, narrative, content, facts, concepts, description,
            content='memories', content_rowid='id',
            tokenize='unicode61'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
            user_prompt, summary,
            content='sessions', content_rowid='id',
            tokenize='unicode61'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS reasoning_chains_fts USING fts5(
            question, outcome_summary, failure_reason, extracted_facts,
            content='reasoning_chains', content_rowid='id',
            tokenize='unicode61'
        );
        """)
    except sqlite3.OperationalError as e:
        _log.warning(f"FTS5 init (may already exist): {e}")

    # Triggers for FTS sync
    for trigger_sql in [
        """CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, title, subtitle, narrative, content, facts, concepts, description)
            VALUES (new.id, new.title, new.subtitle, new.narrative, new.content, new.facts, new.concepts, new.description);
        END""",
        """CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, title, subtitle, narrative, content, facts, concepts, description)
            VALUES('delete', old.id, old.title, old.subtitle, old.narrative, old.content, old.facts, old.concepts, old.description);
        END""",
        """CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, title, subtitle, narrative, content, facts, concepts, description)
            VALUES('delete', old.id, old.title, old.subtitle, old.narrative, old.content, old.facts, old.concepts, old.description);
            INSERT INTO memories_fts(rowid, title, subtitle, narrative, content, facts, concepts, description)
            VALUES (new.id, new.title, new.subtitle, new.narrative, new.content, new.facts, new.concepts, new.description);
        END""",
        """CREATE TRIGGER IF NOT EXISTS rc_ai AFTER INSERT ON reasoning_chains BEGIN
            INSERT INTO reasoning_chains_fts(rowid, question, outcome_summary, failure_reason, extracted_facts)
            VALUES (new.id, new.question, new.outcome_summary, new.failure_reason, new.extracted_facts);
        END""",
        """CREATE TRIGGER IF NOT EXISTS rc_ad AFTER DELETE ON reasoning_chains BEGIN
            INSERT INTO reasoning_chains_fts(reasoning_chains_fts, rowid, question, outcome_summary, failure_reason, extracted_facts)
            VALUES('delete', old.id, old.question, old.outcome_summary, old.failure_reason, old.extracted_facts);
        END""",
        """CREATE TRIGGER IF NOT EXISTS rc_au AFTER UPDATE ON reasoning_chains BEGIN
            INSERT INTO reasoning_chains_fts(reasoning_chains_fts, rowid, question, outcome_summary, failure_reason, extracted_facts)
            VALUES('delete', old.id, old.question, old.outcome_summary, old.failure_reason, old.extracted_facts);
            INSERT INTO reasoning_chains_fts(rowid, question, outcome_summary, failure_reason, extracted_facts)
            VALUES (new.id, new.question, new.outcome_summary, new.failure_reason, new.extracted_facts);
        END""",
    ]:
        try:
            conn.execute(trigger_sql)
        except sqlite3.OperationalError:
            pass

    # Indexes
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_mem_project ON memories(project, category)",
        "CREATE INDEX IF NOT EXISTS idx_mem_active ON memories(is_active, project)",
        "CREATE INDEX IF NOT EXISTS idx_mem_hash ON memories(content_hash, project)",
        "CREATE INDEX IF NOT EXISTS idx_mem_created ON memories(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_mem_session ON memories(origin_session)",
        "CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(memory_type, is_active)",
        "CREATE INDEX IF NOT EXISTS idx_mem_importance ON memories(importance DESC, is_active)",
        "CREATE INDEX IF NOT EXISTS idx_mem_accessed ON memories(last_accessed_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_queue(status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project, status)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_uuid ON sessions(session_uuid)",
        "CREATE INDEX IF NOT EXISTS idx_access_memory ON memory_access_log(memory_id, accessed_at)",
    ]:
        conn.execute(idx_sql)

    conn.commit()
    _log.info("Database initialized")


# ── Memory CRUD ──────────────────────────────────────────────────

def insert_memory(project, category="observation", obs_type=None, memory_type="episodic",
                  title=None, subtitle=None, narrative=None, facts=None, concepts=None,
                  files_read=None, files_modified=None, name=None, description=None,
                  content=None, origin_session=None, generated_by=None, metadata=None,
                  importance=None, supersedes=None, session_phase=None,
                  module_id=None, related_to=None):
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Dedup via content_hash
    raw = json.dumps({"title": title, "narrative": narrative, "content": content, "facts": facts}, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(raw.encode()).hexdigest()[:16]
    existing = conn.execute("SELECT id FROM memories WHERE content_hash=? AND project=? AND is_active=1", (h, project)).fetchone()
    if existing:
        _log.debug(f"Duplicate memory skipped (hash={h})")
        return existing["id"]
    importance = importance or 5
    cur = conn.execute("""
        INSERT INTO memories (project, category, obs_type, memory_type, title, subtitle, narrative,
            facts, concepts, files_read, files_modified, name, description, content,
            origin_session, generated_by, content_hash, created_at, updated_at,
            last_accessed_at, importance, stability, supersedes, session_phase, metadata,
            module_id, related_to)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (project, category, obs_type, memory_type, title, subtitle, narrative,
          json.dumps(facts or [], ensure_ascii=False), json.dumps(concepts or [], ensure_ascii=False),
          json.dumps(files_read or [], ensure_ascii=False), json.dumps(files_modified or [], ensure_ascii=False),
          name, description, content, origin_session, generated_by, h, now, now,
          now, importance, 1.0, supersedes, session_phase,
          metadata if isinstance(metadata, str) else json.dumps(metadata or {}, ensure_ascii=False),
          module_id, json.dumps(related_to or [], ensure_ascii=False)))
    conn.commit()
    _log.info(f"Memory inserted: id={cur.lastrowid} type={category}/{obs_type} mtype={memory_type} importance={importance} project={project}")
    return cur.lastrowid


def get_memory(memory_id):
    conn = connect()
    return conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()


def update_memory(memory_id, **fields):
    conn = connect()
    fields["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _validate_fields(fields, _MEMORY_FIELDS, "memory")
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [memory_id]
    conn.execute(f"UPDATE memories SET {sets} WHERE id=?", vals)
    conn.commit()


def deactivate_memory(memory_id, reason=""):
    update_memory(memory_id, is_active=0, metadata=json.dumps({"deactivated_reason": reason}))


def refresh_memory(memory_id):
    """Update last_accessed_at and increment access_count (FSRS reinforcement)."""
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute("""
        UPDATE memories SET last_accessed_at=?, access_count=access_count+1,
        stability=stability * 1.3, updated_at=?
        WHERE id=?
    """, (now, now, memory_id))
    conn.commit()


def log_memory_access(memory_id, query=None, score=None):
    """Record a memory access for analytics."""
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute("INSERT INTO memory_access_log (memory_id, query, score, accessed_at) VALUES (?,?,?,?)",
                 (memory_id, query, score, now))
    conn.commit()


def list_memories(project=None, category=None, module_id=None, active_only=True, limit=100, offset=0):
    conn = connect()
    sql = "SELECT * FROM memories WHERE 1=1"
    params = []
    if project:
        sql += " AND project=?"
        params.append(project)
    if category:
        sql += " AND category=?"
        params.append(category)
    if module_id:
        sql += " AND module_id=?"
        params.append(module_id)
    if active_only:
        sql += " AND is_active=1"
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return conn.execute(sql, params).fetchall()


def count_memories(project=None):
    conn = connect()
    if project:
        return conn.execute("SELECT COUNT(*) FROM memories WHERE project=? AND is_active=1", (project,)).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM memories WHERE is_active=1").fetchone()[0]


# ── FTS Search ───────────────────────────────────────────────────

def search_fts(query, project=None, limit=20):
    import re
    conn = connect()
    # Split by spaces for English, extract Chinese segments
    words = query.split()
    # Also extract Chinese words (2+ char segments) for FTS matching
    chinese_segments = re.findall(r'[一-鿿]{2,}', query)
    all_words = words + chinese_segments
    fts_query = " OR ".join(f'"{w}"' for w in all_words if w.strip())
    if not fts_query:
        return []
    sql = """
        SELECT m.*, rank AS rank
        FROM memories_fts
        JOIN memories m ON m.id = memories_fts.rowid
        WHERE memories_fts MATCH ? AND m.is_active=1
    """
    params = [fts_query]
    if project:
        sql += " AND m.project=?"
        params.append(project)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        _log.warning(f"FTS search error: {e}")
        return []


def search_fts_in_module(query, module_id, limit=20):
    """FTS search scoped to a specific module."""
    conn = connect()
    fts_query = " OR ".join(f'"{w}"' for w in query.split() if w.strip())
    if not fts_query:
        return []
    sql = """
        SELECT m.*, rank AS rank
        FROM memories_fts
        JOIN memories m ON m.id = memories_fts.rowid
        WHERE memories_fts MATCH ? AND m.is_active=1 AND m.module_id=?
        ORDER BY rank LIMIT ?
    """
    try:
        return conn.execute(sql, [fts_query, module_id, limit]).fetchall()
    except sqlite3.OperationalError as e:
        _log.warning(f"FTS module search error: {e}")
        return []


def search_by_keywords(keywords, project=None, limit=5):
    """Fast keyword-based search for Phase 1 injection (no embedding needed)."""
    conn = connect()
    if not keywords:
        return []
    conditions = " OR ".join(["title LIKE ? OR narrative LIKE ? OR concepts LIKE ?"] * len(keywords))
    params = []
    for kw in keywords:
        like = f"%{kw}%"
        params.extend([like, like, like])
    sql = f"SELECT * FROM memories WHERE is_active=1 AND ({conditions})"
    if project:
        sql += " AND project=?"
        params.append(project)
    sql += " ORDER BY importance DESC, created_at DESC LIMIT ?"
    params.append(limit)
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        _log.warning(f"Keyword search error: {e}")
        return []


# ── Sessions ─────────────────────────────────────────────────────

def upsert_session(session_uuid, project, user_prompt=None):
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    existing = conn.execute("SELECT id FROM sessions WHERE session_uuid=?", (session_uuid,)).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute("INSERT INTO sessions (session_uuid, project, user_prompt, started_at) VALUES (?,?,?,?)",
                       (session_uuid, project, user_prompt, now))
    conn.commit()
    return cur.lastrowid


def complete_session(session_uuid, summary=None):
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute("UPDATE sessions SET completed_at=?, status='completed', summary=? WHERE session_uuid=?",
                 (now, summary, session_uuid))
    conn.commit()


def get_sessions(project=None, limit=50):
    conn = connect()
    if project:
        return conn.execute("SELECT * FROM sessions WHERE project=? ORDER BY started_at DESC LIMIT ?",
                            (project, limit)).fetchall()
    return conn.execute("SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()


def get_session_detail(session_uuid):
    conn = connect()
    session = conn.execute("SELECT * FROM sessions WHERE session_uuid=?", (session_uuid,)).fetchone()
    if not session:
        return None
    memories = conn.execute("""
        SELECT * FROM memories WHERE origin_session=? AND is_active=1 ORDER BY created_at ASC
    """, (session_uuid,)).fetchall()
    interactions = conn.execute("""
        SELECT * FROM pending_queue WHERE session_uuid=? ORDER BY created_at ASC
    """, (session_uuid,)).fetchall()
    return {"session": session, "memories": memories, "interactions": interactions}


def get_session_interactions(session_uuid, limit=200):
    conn = connect()
    return conn.execute("""
        SELECT * FROM pending_queue WHERE session_uuid=?
        ORDER BY created_at ASC LIMIT ?
    """, (session_uuid, limit)).fetchall()


def get_session_interaction_count(session_uuid):
    conn = connect()
    row = conn.execute("SELECT COUNT(*) FROM pending_queue WHERE session_uuid=?", (session_uuid,)).fetchone()
    return row[0] if row else 0


# ── Pending Queue ────────────────────────────────────────────────

def enqueue(session_uuid, project, hook_event, tool_name=None, tool_input=None,
            tool_response=None, cwd=None, extra=None):
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cur = conn.execute("""
        INSERT INTO pending_queue (session_uuid, project, hook_event, tool_name, tool_input,
            tool_response, cwd, extra, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (session_uuid, project, hook_event, tool_name,
          json.dumps(tool_input, ensure_ascii=False) if tool_input else None,
          json.dumps(tool_response, ensure_ascii=False) if tool_response else None,
          cwd, json.dumps(extra or {}, ensure_ascii=False), now))
    conn.commit()
    return cur.lastrowid


def dequeue(limit=10):
    conn = connect()
    rows = conn.execute("""
        SELECT * FROM pending_queue WHERE status='pending'
        ORDER BY created_at ASC LIMIT ?
    """, (limit,)).fetchall()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    conn.execute(f"UPDATE pending_queue SET status='processing' WHERE id IN ({placeholders})", ids)
    conn.commit()
    return rows


def mark_processed(queue_id):
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute("UPDATE pending_queue SET status='done', processed_at=? WHERE id=?", (now, queue_id))
    conn.commit()


def mark_failed(queue_id, error="", max_retries=3):
    """Mark as failed. If retry_count < max_retries, set status='retry' for later re-processing."""
    conn = connect()
    row = conn.execute("SELECT retry_count FROM pending_queue WHERE id=?", (queue_id,)).fetchone()
    retries = (row["retry_count"] or 0) + 1 if row else 1
    if retries <= max_retries:
        conn.execute("UPDATE pending_queue SET status='retry', retry_count=?, last_error=? WHERE id=?",
                     (retries, error[:500], queue_id))
        _log.info(f"Queue item {queue_id} marked for retry ({retries}/{max_retries}): {error[:80]}")
    else:
        conn.execute("UPDATE pending_queue SET status='failed', retry_count=?, last_error=? WHERE id=?",
                     (retries, error[:500], queue_id))
        _log.warning(f"Queue item {queue_id} permanently failed after {retries} retries: {error[:80]}")
    conn.commit()


def dequeue_retryable(limit=5):
    """Get items marked for retry (LLM was unavailable earlier)."""
    conn = connect()
    rows = conn.execute("""
        SELECT * FROM pending_queue WHERE status='retry'
        ORDER BY created_at ASC LIMIT ?
    """, (limit,)).fetchall()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" * len(ids))
    conn.execute(f"UPDATE pending_queue SET status='processing' WHERE id IN ({placeholders})", ids)
    conn.commit()
    return rows


def queue_stats():
    conn = connect()
    return conn.execute("""
        SELECT status, COUNT(*) as cnt FROM pending_queue GROUP BY status
    """).fetchall()


# ── Knowledge Index ──────────────────────────────────────────────

def set_knowledge(project, key, value, memory_id=None):
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute("""
        INSERT INTO knowledge_index (project, key, value, memory_id, updated_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(project, key) DO UPDATE SET value=excluded.value,
            memory_id=excluded.memory_id, updated_at=excluded.updated_at
    """, (project, key, value, memory_id, now))
    conn.commit()


def get_knowledge(project=None, key=None):
    conn = connect()
    if key and project:
        return conn.execute("SELECT * FROM knowledge_index WHERE project=? AND key=?",
                            (project, key)).fetchone()
    if project:
        return conn.execute("SELECT * FROM knowledge_index WHERE project=? ORDER BY key", (project,)).fetchall()
    return conn.execute("SELECT * FROM knowledge_index ORDER BY project, key").fetchall()


# ── Memory Modules ──────────────────────────────────────────────

def get_or_create_module(project, name, description=""):
    """Find existing module by name or create a new one. Returns module id."""
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    existing = conn.execute(
        "SELECT id FROM memory_modules WHERE project=? AND name=?", (project, name)
    ).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO memory_modules (project, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
        (project, name, description, now, now)
    )
    conn.commit()
    _log.info(f"Module created: id={cur.lastrowid} name={name} project={project}")
    return cur.lastrowid


def get_modules(project=None):
    """List all modules. If project is given, filter by project."""
    conn = connect()
    if project:
        return conn.execute(
            "SELECT * FROM memory_modules WHERE project=? ORDER BY memory_count DESC", (project,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM memory_modules ORDER BY memory_count DESC"
    ).fetchall()


def get_module(module_id):
    """Get a single module by id."""
    conn = connect()
    return conn.execute("SELECT * FROM memory_modules WHERE id=?", (module_id,)).fetchone()


def update_module(module_id, **fields):
    """Update module fields."""
    conn = connect()
    fields["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _validate_fields(fields, _MODULE_FIELDS, "module")
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [module_id]
    conn.execute(f"UPDATE memory_modules SET {sets} WHERE id=?", vals)
    conn.commit()


def increment_module_count(module_id):
    """Increment the memory_count for a module."""
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute(
        "UPDATE memory_modules SET memory_count=memory_count+1, updated_at=? WHERE id=?",
        (now, module_id)
    )
    conn.commit()


def get_memories_by_module(module_id, limit=50):
    """Get all active memories in a module."""
    conn = connect()
    return conn.execute(
        "SELECT * FROM memories WHERE module_id=? AND is_active=1 ORDER BY importance DESC, created_at DESC LIMIT ?",
        (module_id, limit)
    ).fetchall()


def recalculate_module_counts(project):
    """Recalculate memory_count for all modules in a project."""
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute("""
        UPDATE memory_modules SET memory_count = (
            SELECT COUNT(*) FROM memories WHERE memories.module_id = memory_modules.id AND memories.is_active = 1
        ), updated_at = ? WHERE project = ?
    """, (now, project))
    conn.commit()


# ── Skills ──────────────────────────────────────────────────────

def insert_skill(project, name, description="", workflow=None, trigger_keywords=None,
                 stop_conditions=None, output_format="", examples=None, gotchas=None,
                 references_list=None, confidence=3, origin_session=None):
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cur = conn.execute("""
        INSERT INTO skills (project, name, description, workflow, trigger_keywords,
            stop_conditions, output_format, examples, gotchas, references_list,
            confidence, origin_session, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project, name) DO UPDATE SET
            description=excluded.description, workflow=excluded.workflow,
            trigger_keywords=excluded.trigger_keywords, stop_conditions=excluded.stop_conditions,
            output_format=excluded.output_format, examples=excluded.examples,
            gotchas=excluded.gotchas, references_list=excluded.references_list,
            confidence=excluded.confidence, origin_session=excluded.origin_session,
            updated_at=excluded.updated_at
    """, (project, name, description,
          json.dumps(workflow or [], ensure_ascii=False),
          json.dumps(trigger_keywords or [], ensure_ascii=False),
          json.dumps(stop_conditions or [], ensure_ascii=False),
          output_format,
          json.dumps(examples or [], ensure_ascii=False),
          json.dumps(gotchas or [], ensure_ascii=False),
          json.dumps(references_list or [], ensure_ascii=False),
          confidence, origin_session, now, now))
    conn.commit()
    _log.info(f"Skill saved: id={cur.lastrowid} name={name} project={project}")
    return cur.lastrowid


def get_skills(project=None, active_only=True, limit=50):
    conn = connect()
    sql = "SELECT * FROM skills WHERE 1=1"
    params = []
    if project:
        sql += " AND project=?"
        params.append(project)
    if active_only:
        sql += " AND is_active=1"
    sql += " ORDER BY confidence DESC, use_count DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def get_skill(skill_id):
    conn = connect()
    return conn.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()


def update_skill(skill_id, **fields):
    conn = connect()
    fields["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _validate_fields(fields, _SKILL_FIELDS, "skill")
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [skill_id]
    conn.execute(f"UPDATE skills SET {sets} WHERE id=?", vals)
    conn.commit()


def deactivate_skill(skill_id):
    update_skill(skill_id, is_active=0)


def use_skill(skill_id):
    """Increment use count and update last used time."""
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute("UPDATE skills SET use_count=use_count+1, last_used_at=?, updated_at=? WHERE id=?",
                 (now, now, skill_id))
    conn.commit()


def search_skills(query, project=None, limit=10):
    """Search skills by name, description, or trigger keywords."""
    conn = connect()
    like = f"%{query}%"
    sql = """SELECT * FROM skills WHERE is_active=1
             AND (name LIKE ? OR description LIKE ? OR trigger_keywords LIKE ?)"""
    params = [like, like, like]
    if project:
        sql += " AND project=?"
        params.append(project)
    sql += " ORDER BY confidence DESC, use_count DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


# ── Fusion Log ───────────────────────────────────────────────────

def log_fusion(action, source_id=None, target_id=None, reason=""):
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute("INSERT INTO fusion_log (action, source_id, target_id, reason, created_at) VALUES (?,?,?,?,?)",
                 (action, source_id, target_id, reason, now))
    conn.commit()


def get_fusion_log(limit=50):
    conn = connect()
    return conn.execute("SELECT * FROM fusion_log ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()


# ── Stats ────────────────────────────────────────────────────────

def stats():
    conn = connect()
    result = {}
    result["total_memories"] = conn.execute("SELECT COUNT(*) FROM memories WHERE is_active=1").fetchone()[0]
    result["total_sessions"] = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    result["pending_queue"] = conn.execute("SELECT COUNT(*) FROM pending_queue WHERE status='pending'").fetchone()[0]
    result["fusion_actions"] = conn.execute("SELECT COUNT(*) FROM fusion_log").fetchone()[0]
    result["total_reasoning_chains"] = conn.execute("SELECT COUNT(*) FROM reasoning_chains WHERE is_active=1").fetchone()[0]
    by_type = conn.execute("SELECT category, COUNT(*) as cnt FROM memories WHERE is_active=1 GROUP BY category").fetchall()
    result["by_category"] = {r["category"]: r["cnt"] for r in by_type}
    by_project = conn.execute("SELECT project, COUNT(*) as cnt FROM memories WHERE is_active=1 GROUP BY project").fetchall()
    result["by_project"] = {r["project"]: r["cnt"] for r in by_project}
    by_obs = conn.execute("SELECT obs_type, COUNT(*) as cnt FROM memories WHERE is_active=1 AND obs_type IS NOT NULL GROUP BY obs_type").fetchall()
    result["by_obs_type"] = {r["obs_type"]: r["cnt"] for r in by_obs}
    by_mtype = conn.execute("SELECT memory_type, COUNT(*) as cnt FROM memories WHERE is_active=1 GROUP BY memory_type").fetchall()
    result["by_memory_type"] = {r["memory_type"]: r["cnt"] for r in by_mtype}
    by_mode = conn.execute("SELECT thinking_mode, COUNT(*) as cnt FROM reasoning_chains WHERE is_active=1 GROUP BY thinking_mode").fetchall()
    result["by_thinking_mode"] = {r["thinking_mode"]: r["cnt"] for r in by_mode}
    by_outcome = conn.execute("SELECT outcome, COUNT(*) as cnt FROM reasoning_chains WHERE is_active=1 GROUP BY outcome").fetchall()
    result["by_outcome"] = {r["outcome"]: r["cnt"] for r in by_outcome}
    return result


# ── Reasoning Chains CRUD ───────────────────────────────────────

def insert_reasoning_chain(project, thinking_mode="cot", question=None, steps=None,
                           outcome="pending", outcome_summary=None, failure_reason=None,
                           extracted_facts=None, session_uuid=None, importance=5,
                           module_id=None):
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cur = conn.execute("""
        INSERT INTO reasoning_chains (project, module_id, session_uuid, thinking_mode, question, steps,
            outcome, outcome_summary, failure_reason,
            extracted_facts, importance, created_at, updated_at, last_accessed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (project, module_id, session_uuid, thinking_mode, question,
          json.dumps(steps or [], ensure_ascii=False),
          outcome, outcome_summary, failure_reason,
          json.dumps(extracted_facts or [], ensure_ascii=False),
          importance, now, now, now))
    conn.commit()
    _log.info(f"Reasoning chain inserted: id={cur.lastrowid} mode={thinking_mode} outcome={outcome} project={project}")
    return cur.lastrowid


def get_reasoning_chain(chain_id):
    conn = connect()
    return conn.execute("SELECT * FROM reasoning_chains WHERE id=?", (chain_id,)).fetchone()


def update_reasoning_chain(chain_id, **fields):
    conn = connect()
    fields["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for k in ("steps", "extracted_facts"):
        if k in fields and isinstance(fields[k], list):
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    _validate_fields(fields, _REASONING_FIELDS, "reasoning_chain")
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [chain_id]
    conn.execute(f"UPDATE reasoning_chains SET {sets} WHERE id=?", vals)
    conn.commit()


def refresh_reasoning_chain(chain_id):
    conn = connect()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute("""
        UPDATE reasoning_chains SET last_accessed_at=?, access_count=access_count+1, updated_at=?
        WHERE id=?
    """, (now, now, chain_id))
    conn.commit()


def list_reasoning_chains(project=None, thinking_mode=None, outcome=None,
                          module_id=None, active_only=True, limit=50, offset=0):
    conn = connect()
    sql = "SELECT * FROM reasoning_chains WHERE 1=1"
    params = []
    if project:
        sql += " AND project=?"
        params.append(project)
    if module_id:
        sql += " AND module_id=?"
        params.append(module_id)
    if thinking_mode:
        sql += " AND thinking_mode=?"
        params.append(thinking_mode)
    if outcome:
        sql += " AND outcome=?"
        params.append(outcome)
    if active_only:
        sql += " AND is_active=1"
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return conn.execute(sql, params).fetchall()


def search_reasoning_chains_fts(query, project=None, limit=10):
    conn = connect()
    fts_query = " OR ".join(f'"{w}"' for w in query.split() if w.strip())
    if not fts_query:
        return []
    sql = """
        SELECT rc.*, rank AS rank
        FROM reasoning_chains_fts
        JOIN reasoning_chains rc ON rc.id = reasoning_chains_fts.rowid
        WHERE reasoning_chains_fts MATCH ? AND rc.is_active=1
    """
    params = [fts_query]
    if project:
        sql += " AND rc.project=?"
        params.append(project)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        _log.warning(f"Reasoning chains FTS search error: {e}")
        return []


def count_reasoning_chains(project=None):
    conn = connect()
    if project:
        return conn.execute("SELECT COUNT(*) FROM reasoning_chains WHERE project=? AND is_active=1", (project,)).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM reasoning_chains WHERE is_active=1").fetchone()[0]


def find_similar_reasoning_chains(question, project, threshold=0.5, limit=3):
    """Find similar reasoning chains by FTS match on question.

    Returns list of (chain, rank) tuples, most similar first.
    """
    conn = connect()
    # Build FTS query from question words
    words = [w for w in question.replace('"', '').split() if len(w) >= 2]
    if not words:
        return []
    fts_query = " OR ".join(f'"{w}"' for w in words[:10])
    sql = """
        SELECT rc.*, rank
        FROM reasoning_chains_fts
        JOIN reasoning_chains rc ON rc.id = reasoning_chains_fts.rowid
        WHERE reasoning_chains_fts MATCH ? AND rc.is_active=1 AND rc.project=?
        ORDER BY rank
        LIMIT ?
    """
    try:
        rows = conn.execute(sql, (fts_query, project, limit)).fetchall()
        return [(dict(r), r["rank"]) for r in rows]
    except sqlite3.OperationalError:
        return []


def merge_reasoning_chains(old_id, new_data):
    """Merge a new reasoning chain into an existing one.

    Updates the existing chain with richer information from the new extraction.
    """
    conn = connect()
    # Get existing chain
    existing = conn.execute("SELECT * FROM reasoning_chains WHERE id=?", (old_id,)).fetchone()
    if not existing:
        return False

    # Merge: prefer more complete data
    updates = {}
    if new_data.get("outcome") and new_data["outcome"] != "pending":
        updates["outcome"] = new_data["outcome"]
    if new_data.get("outcome_summary"):
        old_summary = existing["outcome_summary"] or ""
        if len(new_data["outcome_summary"]) > len(old_summary):
            updates["outcome_summary"] = new_data["outcome_summary"]
    if new_data.get("failure_reason") and not existing["failure_reason"]:
        updates["failure_reason"] = new_data["failure_reason"]
    if new_data.get("extracted_facts"):
        try:
            old_facts = json.loads(existing["extracted_facts"] or "[]")
        except (json.JSONDecodeError, TypeError):
            old_facts = []
        new_facts = new_data["extracted_facts"]
        merged = list(set(old_facts + new_facts))
        if len(merged) > len(old_facts):
            updates["extracted_facts"] = json.dumps(merged, ensure_ascii=False)
    if new_data.get("steps"):
        try:
            old_steps = json.loads(existing["steps"] or "[]") if isinstance(existing["steps"], str) else (existing["steps"] or [])
        except (json.JSONDecodeError, TypeError):
            old_steps = []
        if len(new_data["steps"]) > len(old_steps):
            updates["steps"] = json.dumps(new_data["steps"], ensure_ascii=False)
    if new_data.get("importance") and (new_data["importance"] or 0) > (existing["importance"] or 0):
        updates["importance"] = new_data["importance"]

    if updates:
        updates["access_count"] = (existing["access_count"] or 0) + 1
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [old_id]
        conn.execute(f"UPDATE reasoning_chains SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?", vals)
        conn.commit()

    return True


def cleanup_low_quality_reasoning_chains(min_importance=4):
    """Remove low-quality reasoning chains.

    Criteria for removal:
    - importance < min_importance
    - outcome='failure' with no failure_reason (no actionable info)
    - No steps (empty reasoning)
    """
    conn = connect()

    # 1. Remove low-importance chains
    r1 = conn.execute(
        "UPDATE reasoning_chains SET is_active=0 WHERE is_active=1 AND importance < ?",
        (min_importance,)
    ).rowcount

    # 2. Remove failure chains with no reason
    r2 = conn.execute(
        "UPDATE reasoning_chains SET is_active=0 WHERE is_active=1 AND outcome='failure' AND (failure_reason IS NULL OR failure_reason='')"
    ).rowcount

    # 3. Remove chains with no steps
    r3 = conn.execute(
        "UPDATE reasoning_chains SET is_active=0 WHERE is_active=1 AND (steps IS NULL OR steps='[]' OR steps='null')"
    ).rowcount

    conn.commit()
    total = r1 + r2 + r3
    return {"deactivated": total, "low_importance": r1, "failure_no_reason": r2, "no_steps": r3}


def cleanup_generic_memories():
    """Remove memories with generic/useless narratives.

    Criteria for removal:
    - narrative contains generic phrases like "从推理中提取的知识"
    - title starts with "推理提取:" (old format)
    - narrative is too short (< 20 chars) and has no facts
    """
    conn = connect()

    # 1. Remove "推理提取" memories with generic narrative
    r1 = conn.execute("""
        UPDATE memories SET is_active=0 WHERE is_active=1
        AND title LIKE '推理提取:%'
        AND (narrative LIKE '%从推理中提取的知识%' OR narrative LIKE '%推理中提取%')
    """).rowcount

    # 2. Remove memories with very short narrative and no facts
    r2 = conn.execute("""
        UPDATE memories SET is_active=0 WHERE is_active=1
        AND (narrative IS NULL OR length(narrative) < 20)
        AND (facts IS NULL OR facts = '[]' OR facts = 'null')
    """).rowcount

    # 3. Remove memories with "从推理中提取的知识" in narrative
    r3 = conn.execute("""
        UPDATE memories SET is_active=0 WHERE is_active=1
        AND narrative LIKE '%从推理中提取的知识%'
    """).rowcount

    conn.commit()
    total = r1 + r2 + r3
    return {"deactivated": total, "old_reasoning_format": r1, "no_narrative_or_facts": r2, "generic_narrative": r3}

# Memory Modules: Task-Aware Memory Classification and Injection

## Summary

Transform the flat memory store into a module-based architecture where memories are automatically clustered by task domain (10-30 modules per project), inter-memory links are established at save time, and context injection is scoped to the current task's module.

## Motivation

Current injection strategy is project-wide: Phase 1 injects the top-3 high-importance memories across the entire project. As memory grows, this becomes noisy — debugging session gets frontend memories mixed in. The user wants task-scoped injection: only the relevant module's memories are injected initially, with cross-module retrieval on demand.

## Design Decisions

- **Auto-clustering by LLM**: No preset categories. LLM assigns each memory to a module on save, creating new modules as needed.
- **Medium granularity**: 10-30 modules per project. LLM prompt constrains to prefer existing modules.
- **Hybrid linking**: Save-time LLM `related_to` IDs + vector similarity at retrieval time.
- **Module embedding**: Aggregated from member memories, used for fast task-to-module matching.

## Data Model

### New table: `memory_modules`

```sql
CREATE TABLE IF NOT EXISTS memory_modules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project       TEXT NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT,
    memory_count  INTEGER DEFAULT 0,
    embedding     BLOB,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE(project, name)
);
```

### `memories` table additions

```sql
ALTER TABLE memories ADD COLUMN module_id INTEGER;
ALTER TABLE memories ADD COLUMN related_to TEXT DEFAULT '[]';
```

## Observer Changes

### EXTRACTION_SYSTEM prompt additions

Add to the JSON output spec:
- `module`: kebab-case module name (e.g., "auth-system", "db-migration")
- `module_description`: description if new module
- `related_memory_ids`: array of 1-5 related existing memory IDs

### Module assignment flow

1. Query existing modules for the project
2. Inject module list into LLM prompt
3. LLM returns module name + related IDs
4. Find or create module
5. Set `module_id` on memory
6. Set `related_to` on memory
7. Update module `memory_count` and `embedding`

## Injection Strategy

### Phase 1 (first message): Module-scoped index

1. Embed user message
2. Cosine similarity against all `memory_modules.embedding`
3. Best match module → inject its memory titles (max 10, ~300 tokens)
4. If no module scores > 0.3 → fall back to project-wide injection

### Phase 2 (messages 2-3): Module-scoped retrieval

1. Three-factor search within best-match module
2. If < 3 results → expand to related modules via `related_to` links
3. Inject top-5 details, ~500 tokens

### Phase 3 (message 4+): On-demand with topic switching

1. Detect topic switch (embedding drift from current module)
2. If switch → re-match module, search new module
3. If same → deep search + link expansion
4. Only inject when user references past

## Implementation Order

1. `lib/db.py` — schema + CRUD
2. `lib/observer.py` — module assignment + linking
3. `lib/retriever.py` — module retrieval + embedding
4. `lib/hook.py` — module-aware injection
5. `lib/fusion.py` — module embedding update
6. `lib/server.py` — module API
7. `templates/index.html` — module view

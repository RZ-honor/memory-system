# Memory Architecture Analysis

## 1. Long-term vs Short-term Memory Storage

### Storage Layer: Single SQLite Database

```
┌─────────────────────────────────────────────────────────────┐
│                    SQLite Database                           │
├─────────────────────────────────────────────────────────────┤
│  LONG-TERM MEMORY (Persistent)                              │
│  ┌─────────────────┐  ┌─────────────────┐                  │
│  │ memories         │  │ reasoning_chains │                  │
│  │ - episodic       │  │ - steps          │                  │
│  │ - semantic       │  │ - outcome        │                  │
│  │ - procedural     │  │ - extracted_facts│                  │
│  │ - reflective     │  │ - embedding      │                  │
│  └─────────────────┘  └─────────────────┘                  │
│  ┌─────────────────┐  ┌─────────────────┐                  │
│  │ knowledge_index  │  │ memory_modules   │                  │
│  │ - evolved facts  │  │ - topic clusters │                  │
│  │ - key-value      │  │ - embeddings     │                  │
│  └─────────────────┘  └─────────────────┘                  │
│  ┌─────────────────┐                                        │
│  │ skills           │                                        │
│  │ - reusable flows │                                        │
│  └─────────────────┘                                        │
├─────────────────────────────────────────────────────────────┤
│  SHORT-TERM MEMORY (Session-based)                          │
│  ┌─────────────────┐  ┌─────────────────┐                  │
│  │ sessions         │  │ pending_queue    │                  │
│  │ - active/completed│ │ - processing     │                  │
│  └─────────────────┘  └─────────────────┘                  │
│  ┌─────────────────┐                                        │
│  │ memory_access_log│                                        │
│  │ - analytics      │                                        │
│  └─────────────────┘                                        │
└─────────────────────────────────────────────────────────────┘
```

### Memory Types

| Type | Description | Example |
|------|-------------|---------|
| **episodic** | Specific events | "Fixed GAN gradient penalty bug" |
| **semantic** | Facts and knowledge | "User prefers batch_size=32" |
| **procedural** | How-to steps | "How to deploy model" |
| **reflective** | Lessons learned | "Always check GPU memory first" |

---

## 2. Forgetting Mechanism: FSRS Decay Model

### Formula

```
R(t) = (1 + t/(9×S))^(-1)

where:
  R = retention (0.0 to 1.0)
  t = hours since last review
  S = stability (starts at 1.0)
  9 = FSRS decay factor
```

### Decay Behavior

```
Time since access    Stability=1.0    Stability=2.0
─────────────────────────────────────────────────
1 hour               0.90             0.95
1 day (24h)          0.27             0.47
1 week (168h)        0.05             0.10
1 month (720h)       0.01             0.02
```

### Key Mechanisms

```python
# 1. Stability boost on access (FSRS reinforcement)
def refresh_memory(memory_id):
    stability *= 1.3  # 30% boost each time accessed

# 2. Retention threshold for cleanup
RETENTION_THRESHOLD = 0.1  # Below this → deactivate

# 3. Automatic pruning cycle (every 6 hours)
def run_pruning_cycle():
    _expire_stale()      # Remove low-retention memories
    _apply_fsrs_decay()  # Update retention metadata
    _deduplicate()       # Remove duplicate content
    _generalize_patterns() # Extract common patterns
    _remove_low_quality()  # Remove noise
```

### Pruning Pipeline

```
Memory → Check Retention → R < 0.1? → Deactivate
                ↓
        Check Duplicate → Same hash? → Deactivate
                ↓
        Check Quality → quality < 3? → Deactivate
                ↓
        Check Similarity → Title overlap > 50%? → Merge
                ↓
        Extract Patterns → Similar bugs? → Create generalized memory
```

---

## 3. Embedding Model Usage

### Model: all-MiniLM-L6-v2

```
Dimensions: 384
Size: ~80MB
Speed: ~10ms per sentence (CPU)
```

### Three-Factor Retrieval

```python
Score = 0.4 × Recency + 0.3 × Importance + 0.3 × Relevance

where:
  Recency = 0.995 ^ hours_since_access
  Importance = importance / 10.0
  Relevance = FTS_score × 0.4 + Vector_score × 0.6
```

### Embedding Applications

| Component | Usage | Update Frequency |
|-----------|-------|------------------|
| **memories** | Individual memory embeddings | On insert |
| **memory_modules** | Mean pooling of member embeddings | On fusion cycle |
| **reasoning_chains** | Chain embedding for semantic search | On insert |

### Vector Search Flow

```
Query → Compute embedding → Cosine similarity with all memories
                          → Return top-K by score
```

---

## 4. Chain of Thought (Reasoning) Memory

### Schema

```sql
CREATE TABLE reasoning_chains (
    id              INTEGER PRIMARY KEY,
    project         TEXT NOT NULL,
    module_id       INTEGER,          -- Link to topic module
    session_uuid    TEXT,
    thinking_mode   TEXT DEFAULT 'cot', -- Chain of thought
    question        TEXT,              -- Original question
    steps           TEXT DEFAULT '[]', -- JSON array of steps
    outcome         TEXT DEFAULT 'pending', -- success/failure/partial
    outcome_summary TEXT,
    failure_reason  TEXT,
    extracted_facts TEXT DEFAULT '[]', -- Facts derived from reasoning
    embedding       BLOB,             -- Vector embedding
    importance      INTEGER DEFAULT 5,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT,
    updated_at      TEXT,
    last_accessed_at TEXT,
    access_count    INTEGER DEFAULT 0
);
```

### Step Structure

```json
{
  "steps": [
    {
      "thought": "The user wants to optimize training",
      "action": "Checked GPU memory usage",
      "observation": "Only 2GB free out of 8GB"
    },
    {
      "thought": "Need to reduce batch size",
      "action": "Changed batch_size from 32 to 16",
      "observation": "Training stable now"
    }
  ]
}
```

### Reasoning Chain Lifecycle

```
User Question → AI Response → Extract Reasoning Chain
                              ├── question
                              ├── steps (thought/action/observation)
                              ├── outcome (success/failure/partial)
                              ├── extracted_facts
                              └── importance

Search: FTS5 + Vector similarity → Return relevant chains
Access: Update last_accessed_at, increment access_count
```

---

## 5. Fusion and Integration System

### Fusion Cycle Components

```python
def run_fusion_cycle(project):
    _detect_contradictions(project)  # LLM-based comparison
    _merge_similar(project)          # Combine similar memories
    _evolve_knowledge(project)       # Extract facts to knowledge_index
    retriever.update_all_module_embeddings(project)  # Recompute embeddings
```

### Contradiction Detection (Temporal Versioning)

```
New Memory (id=100) contradicts Old Memory (id=50)

Action:
  1. Set new_memory.supersedes = 50
  2. Deactivate old memory (is_active=0)
  3. Log fusion action

Result:
  - Old memory preserved in DB (not deleted)
  - New memory has pointer to old one
  - Full audit trail maintained
```

### Memory Merging

```
Similar Memories (title overlap > 60%):
  - "Fixed GAN gradient issue" (id=10)
  - "GAN gradient penalty fix" (id=15)

Action:
  1. LLM merges into comprehensive memory
  2. Keep one (id=10), deactivate other (id=15)
  3. Update kept memory with merged content

Result:
  - Single comprehensive memory
  - Deduplicated facts
  - Source tracking via fusion_log
```

### Knowledge Evolution

```
Memory with facts:
  - "User prefers batch_size=32"
  - "GPU memory limit is 8GB"

Action:
  1. Extract facts to knowledge_index
  2. Create key-value entries:
     - "batch_size_preference" → "32"
     - "gpu_memory_limit" → "8GB"

Result:
  - Fast key-value lookup
  - Persistent across sessions
  - Updated when new facts arrive
```

---

## 6. Integration: How Components Work Together

### Memory Lifecycle

```
┌─────────────────────────────────────────────────────────────┐
│                    MEMORY LIFECYCLE                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. CREATION                                                │
│     Tool interaction → Observer extracts → Save to DB       │
│     ├── Compute embedding (all-MiniLM-L6-v2)               │
│     ├── Assign to module (topic cluster)                    │
│     └── Set importance (1-10)                               │
│                                                             │
│  2. RETRIEVAL                                               │
│     Query → Three-factor search                            │
│     ├── FTS5 (keyword match)                               │
│     ├── Vector (semantic match)                            │
│     └── Score = 0.4×recency + 0.3×importance + 0.3×relevance│
│                                                             │
│  3. ACCESS                                                  │
│     Memory accessed → FSRS reinforcement                   │
│     ├── stability *= 1.3                                   │
│     ├── last_accessed_at = now                             │
│     └── access_count += 1                                  │
│                                                             │
│  4. MAINTENANCE (every 6 hours)                            │
│     Pruning cycle                                          │
│     ├── Expire low-retention (R < 0.1)                     │
│     ├── Deduplicate (hash match)                           │
│     ├── Remove low quality (quality < 3)                   │
│     └── Generalize patterns (similar bugs → best practice) │
│                                                             │
│  5. FUSION (every 5 minutes)                               │
│     Integration cycle                                      │
│     ├── Detect contradictions (LLM)                        │
│     ├── Merge similar (title overlap > 60%)                │
│     ├── Evolve knowledge (facts → knowledge_index)         │
│     └── Update module embeddings                           │
│                                                             │
│  6. DECAY                                                   │
│     FSRS retention drops                                   │
│     ├── R(t) = (1 + t/(9×S))^(-1)                         │
│     ├── No access → stability stays low                    │
│     └── R < 0.1 → Deactivate                              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Component Interaction

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Observer   │────▶│   Database   │◀────│   Retriever  │
│  (Extract)   │     │  (SQLite)    │     │   (Search)   │
└──────────────┘     └──────┬───────┘     └──────────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │  Fusion  │ │  Pruner  │ │  Modules │
        │ (Merge)  │ │ (Forget) │ │ (Cluster)│
        └──────────┘ └──────────┘ └──────────┘
              │            │            │
              └────────────┼────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │  Knowledge   │
                    │   Index      │
                    │  (Evolved)   │
                    └──────────────┘
```

---

## 7. Current Issues and Recommendations

### Issues Found

| Issue | Impact | Recommendation |
|-------|--------|----------------|
| Module threshold 0.3 too low | Weak matches included | ✅ Fixed: Changed to 0.5 |
| No progressive retrieval | Same search for all messages | ✅ Fixed: Added message_count |
| No web search integration | Missing real-time info | ✅ Fixed: Added search_with_web_fallback |
| Reasoning chains not fused | No cross-session learning | Add reasoning chain fusion |

### Recommended Enhancements

1. **Reasoning Chain Fusion**
   - Detect similar reasoning patterns across sessions
   - Merge successful approaches into procedural memories

2. **Cross-Project Knowledge Transfer**
   - Extract通用 patterns from project-specific memories
   - Create global knowledge base

3. **Adaptive Thresholds**
   - Adjust importance thresholds based on project maturity
   - Learn from user feedback on memory relevance

4. **Memory Compression**
   - Compress old memories (summarize episodic → semantic)
   - Reduce storage while preserving key facts

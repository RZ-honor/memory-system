"""Three-factor retriever: recency + importance + relevance (Generative Agents model).

Based on Park et al. (2023) "Generative Agents: Interactive Simulacra of Human Behavior".
Score = alpha * recency + beta * importance + gamma * relevance
where:
  recency = 0.995 ^ hours_since_last_access
  importance = memory.importance / 10.0
  relevance = cosine_similarity(query_embedding, memory_embedding)
"""
import json, time, math
from lib import db, logger

_log = logger.get()

# Lazy-loaded embedding model
_model = None
_model_name = None

# Three-factor weights (tunable)
ALPHA_RECENCY = 0.4
BETA_IMPORTANCE = 0.3
GAMMA_RELEVANCE = 0.3
RECENCY_DECAY = 0.995  # per hour


def _get_embedding_model():
    global _model, _model_name
    from lib import config
    cfg = config.get("embedding") or {}
    if not cfg.get("enabled", False):
        return None
    target_name = cfg.get("model_name", "all-MiniLM-L6-v2")
    if _model is not None and _model_name == target_name:
        return _model
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(target_name)
        _model_name = target_name
        _log.info(f"Loaded embedding model: {target_name}")
        return _model
    except ImportError:
        _log.warning("sentence-transformers not installed, vector search disabled")
        return None
    except Exception as e:
        _log.warning(f"Failed to load embedding model: {e}")
        return None


def compute_embedding(text):
    """Compute embedding vector for text."""
    model = _get_embedding_model()
    if model is None:
        return None
    try:
        vec = model.encode(text, normalize_embeddings=True)
        return vec.tobytes()
    except Exception as e:
        _log.warning(f"Embedding computation failed: {e}")
        return None


def update_embedding(memory_id, text):
    """Compute and store embedding for a memory."""
    emb_bytes = compute_embedding(text)
    if emb_bytes:
        db.update_memory(memory_id, embedding=emb_bytes)


def update_module_embedding(module_id):
    """Recompute module embedding as the mean of its member memories' embeddings."""
    try:
        import numpy as np
    except ImportError:
        return

    conn = db.connect()
    rows = conn.execute(
        "SELECT embedding FROM memories WHERE module_id=? AND is_active=1 AND embedding IS NOT NULL",
        (module_id,)
    ).fetchall()

    if not rows:
        return

    vectors = []
    for r in rows:
        try:
            vec = np.frombuffer(r["embedding"], dtype=np.float32)
            vectors.append(vec)
        except Exception:
            continue

    if not vectors:
        return

    # Mean pooling
    mean_vec = np.mean(vectors, axis=0)
    # Normalize
    norm = np.linalg.norm(mean_vec)
    if norm > 0:
        mean_vec = mean_vec / norm

    emb_bytes = mean_vec.astype(np.float32).tobytes()
    db.update_module(module_id, embedding=emb_bytes)
    _log.debug(f"Module {module_id} embedding updated ({len(vectors)} memories)")


def update_all_module_embeddings(project):
    """Recompute embeddings for all modules in a project."""
    modules = db.get_modules(project)
    for m in modules:
        update_module_embedding(m["id"])


def _recency_score(last_accessed_at):
    """Exponential decay: 0.995 ^ hours_since_last_access."""
    if not last_accessed_at:
        return 0.5
    try:
        accessed = time.mktime(time.strptime(last_accessed_at[:19], "%Y-%m-%dT%H:%M:%S"))
        hours = (time.time() - accessed) / 3600.0
        return RECENCY_DECAY ** hours
    except (ValueError, TypeError):
        return 0.5


def _importance_score(importance):
    """Normalize importance from 1-10 to 0.0-1.0."""
    if importance is None:
        return 0.5
    return max(0.0, min(1.0, importance / 10.0))


def search(query, project=None, limit=10, category=None, use_vector=True):
    """Three-factor hybrid search: recency + importance + relevance."""
    results = {}

    # 1. FTS5 search for relevance candidates
    fts_results = db.search_fts(query, project=project, limit=limit * 3)
    for r in fts_results:
        mid = r["id"]
        results[mid] = {"memory": dict(r), "fts_score": abs(r["rank"])}

    # 2. Vector search for relevance candidates
    if use_vector:
        model = _get_embedding_model()
        if model is not None:
            vec_results = _vector_search(query, project, limit * 3)
            for mid, score in vec_results:
                if mid in results:
                    results[mid]["vector_score"] = score
                else:
                    mem = db.get_memory(mid)
                    if mem:
                        results[mid] = {"memory": dict(mem), "vector_score": score}

    # 3. Three-factor scoring
    now = time.time()
    for mid, data in results.items():
        mem = data["memory"]
        # Relevance: combine FTS and vector
        fts = data.get("fts_score", 0.0)
        vec = data.get("vector_score", 0.0)
        relevance = fts * 0.4 + vec * 0.6 if vec > 0 else fts * 0.4

        # Recency
        recency = _recency_score(mem.get("last_accessed_at"))

        # Importance
        importance = _importance_score(mem.get("importance", 5))

        # Combined score
        data["score"] = (ALPHA_RECENCY * recency +
                         BETA_IMPORTANCE * importance +
                         GAMMA_RELEVANCE * relevance)
        data["recency"] = recency
        data["importance_val"] = importance
        data["relevance"] = relevance

    # 4. Sort by combined score
    sorted_results = sorted(results.values(), key=lambda x: x["score"], reverse=True)

    # Filter by category if specified
    if category:
        sorted_results = [r for r in sorted_results if r["memory"].get("category") == category]

    # 5. Refresh access metadata for returned memories
    for r in sorted_results[:limit]:
        mid = r["memory"]["id"]
        db.refresh_memory(mid)
        db.log_memory_access(mid, query=query, score=r["score"])

    return [r["memory"] for r in sorted_results[:limit]]


def retrieve_for_context(query, project=None, limit=10, phase=1):
    """Progressive retrieval based on session phase.

    Phase 1 (first message): keyword match, top 3, fast
    Phase 2 (messages 2-3): three-factor search, top 10
    Phase 3 (message 4+): only on-demand, caller decides
    """
    if phase == 1:
        # Fast keyword match, no embedding needed
        keywords = extract_keywords(query)
        results = db.search_by_keywords(keywords, project=project, limit=min(limit, 5))
        for r in results:
            db.refresh_memory(r["id"])
        return results
    elif phase >= 2:
        # Full three-factor search
        return search(query, project=project, limit=limit)
    return []


def extract_keywords(text):
    """Extract meaningful keywords from text for fast search."""
    # Simple keyword extraction: split, filter stopwords and short words
    stopwords = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                 "have", "has", "had", "do", "does", "did", "will", "would",
                 "could", "should", "may", "might", "can", "shall",
                 "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
                 "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
                 "你", "会", "着", "没有", "看", "好", "自己", "这"}
    words = text.replace(",", " ").replace(".", " ").replace("?", " ").split()
    return [w for w in words if len(w) > 1 and w.lower() not in stopwords][:10]


def _vector_search(query, project, limit):
    """Search by vector similarity using stored embeddings."""
    try:
        import numpy as np
    except ImportError:
        _log.debug("numpy not available, skipping vector search")
        return []

    conn = db.connect()
    query_vec = compute_embedding(query)
    if query_vec is None:
        return []

    query_np = np.frombuffer(query_vec, dtype=np.float32)

    sql = "SELECT id, embedding FROM memories WHERE is_active=1 AND embedding IS NOT NULL"
    params = []
    if project:
        sql += " AND project=?"
        params.append(project)

    rows = conn.execute(sql, params).fetchall()
    scores = []
    for r in rows:
        try:
            mem_vec = np.frombuffer(r["embedding"], dtype=np.float32)
            sim = float(np.dot(query_np, mem_vec))
            scores.append((r["id"], sim))
        except Exception:
            continue

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:limit]


def format_for_context(memories, max_chars=3000):
    """Format memories into a context string for LLM injection."""
    if not memories:
        return ""
    lines = []
    total = 0
    for m in memories:
        cat = m.get("category", "")
        mtype = m.get("memory_type", "")
        title = m.get("title") or m.get("name") or ""
        narrative = m.get("narrative") or m.get("description") or ""
        importance = m.get("importance", 5)
        entry = f"[{cat}/{mtype}] {title}"
        if narrative:
            entry += f" — {narrative[:120]}"
        if importance >= 8:
            entry += " ★"
        if total + len(entry) > max_chars:
            break
        lines.append(entry)
        total += len(entry)
    return "\n".join(lines)


# ── Module-Aware Retrieval ──────────────────────────────────────

def match_module(query, project):
    """Find the best matching module for a query using embedding similarity.

    Returns (module_id, module_name, similarity_score) or (None, None, 0.0) if no good match.
    """
    model = _get_embedding_model()
    if model is None:
        # Fallback: keyword match on module names
        return _match_module_keyword(query, project)

    modules = db.get_modules(project)
    if not modules:
        return None, None, 0.0

    # Compute query embedding
    try:
        query_vec = model.encode(query, normalize_embeddings=True)
    except Exception as e:
        _log.warning(f"Query embedding failed: {e}")
        return _match_module_keyword(query, project)

    import numpy as np
    best_id, best_name, best_score = None, None, 0.0

    for m in modules:
        if m["embedding"] is None:
            continue
        try:
            mod_vec = np.frombuffer(m["embedding"], dtype=np.float32)
            sim = float(np.dot(query_vec, mod_vec))
            if sim > best_score:
                best_id, best_name, best_score = m["id"], m["name"], sim
        except Exception:
            continue

    return best_id, best_name, best_score


def _match_module_keyword(query, project):
    """Fallback module matching using keyword overlap. Supports Chinese."""
    modules = db.get_modules(project)
    if not modules:
        return None, None, 0.0

    import re
    query_lower = query.lower()
    query_words = set(query_lower.split())
    # Chinese: extract character bigrams
    chinese_chars = re.findall(r'[一-鿿]+', query_lower)
    for seg in chinese_chars:
        for i in range(len(seg) - 1):
            query_words.add(seg[i:i+2])
        if len(seg) >= 2:
            query_words.add(seg)

    best_id, best_name, best_score = None, None, 0.0

    for m in modules:
        name = m["name"].lower().replace("-", " ").replace("_", " ")
        name_words = set(name.split())
        name_chinese = re.findall(r'[一-鿿]+', name)
        for seg in name_chinese:
            for i in range(len(seg) - 1):
                name_words.add(seg[i:i+2])
            if len(seg) >= 2:
                name_words.add(seg)
        desc_words = set((m["description"] or "").lower().split())
        all_words = name_words | desc_words
        overlap = len(query_words & all_words)
        if overlap > best_score:
            best_id, best_name, best_score = m["id"], m["name"], float(overlap)

    return best_id, best_name, best_score


def search_in_module(query, module_id, limit=10, use_vector=True):
    """Three-factor search scoped to a specific module."""
    results = {}

    # 1. FTS search within module
    fts_results = db.search_fts_in_module(query, module_id, limit=limit * 3)
    for r in fts_results:
        mid = r["id"]
        results[mid] = {"memory": dict(r), "fts_score": abs(r["rank"])}

    # 2. Vector search within module
    if use_vector:
        model = _get_embedding_model()
        if model is not None:
            vec_results = _vector_search_in_module(query, module_id, limit * 3)
            for mid, score in vec_results:
                if mid in results:
                    results[mid]["vector_score"] = score
                else:
                    mem = db.get_memory(mid)
                    if mem:
                        results[mid] = {"memory": dict(mem), "vector_score": score}

    # 3. Three-factor scoring
    for mid, data in results.items():
        mem = data["memory"]
        fts = data.get("fts_score", 0.0)
        vec = data.get("vector_score", 0.0)
        relevance = fts * 0.4 + vec * 0.6 if vec > 0 else fts * 0.4
        recency = _recency_score(mem.get("last_accessed_at"))
        importance = _importance_score(mem.get("importance", 5))
        data["score"] = (ALPHA_RECENCY * recency +
                         BETA_IMPORTANCE * importance +
                         GAMMA_RELEVANCE * relevance)

    sorted_results = sorted(results.values(), key=lambda x: x["score"], reverse=True)

    # Refresh access
    for r in sorted_results[:limit]:
        mid = r["memory"]["id"]
        db.refresh_memory(mid)
        db.log_memory_access(mid, query=query, score=r["score"])

    return [r["memory"] for r in sorted_results[:limit]]


def _vector_search_in_module(query, module_id, limit):
    """Vector search scoped to a specific module."""
    try:
        import numpy as np
    except ImportError:
        return []

    conn = db.connect()
    query_vec = compute_embedding(query)
    if query_vec is None:
        return []

    query_np = np.frombuffer(query_vec, dtype=np.float32)
    rows = conn.execute(
        "SELECT id, embedding FROM memories WHERE is_active=1 AND embedding IS NOT NULL AND module_id=?",
        (module_id,)
    ).fetchall()

    scores = []
    for r in rows:
        try:
            mem_vec = np.frombuffer(r["embedding"], dtype=np.float32)
            sim = float(np.dot(query_np, mem_vec))
            scores.append((r["id"], sim))
        except Exception:
            continue

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:limit]


def get_module_index(project, max_chars=500):
    """Get a compact index of all modules for a project (for Phase 1 injection).

    Returns a formatted string with module names, descriptions, and memory counts.
    """
    modules = db.get_modules(project)
    if not modules:
        return ""

    lines = []
    total = 0
    for m in modules:
        desc = m["description"] or ""
        entry = f"- {m['name']}: {desc} ({m['memory_count']}条)"
        if total + len(entry) > max_chars:
            break
        lines.append(entry)
        total += len(entry)

    return "\n".join(lines)


def get_module_memories_index(module_id, limit=10, max_chars=500):
    """Get a compact index of memories in a module (for Phase 1 injection).

    Returns a formatted string with memory titles and types.
    """
    memories = db.get_memories_by_module(module_id, limit=limit)
    if not memories:
        return ""

    lines = []
    total = 0
    for m in memories:
        cat = m.get("category", "")
        mtype = m.get("memory_type", "")
        title = m.get("title") or ""
        importance = m.get("importance", 5)
        star = " ★" if importance >= 8 else ""
        entry = f"  [{cat}/{mtype}] {title}{star}"
        if total + len(entry) > max_chars:
            break
        lines.append(entry)
        total += len(entry)

    return "\n".join(lines)


def expand_via_links(memory_ids, project, limit=5):
    """Expand retrieval results by following related_to links.

    Given a list of memory IDs, find their related memories and return additional results.
    """
    if not memory_ids:
        return []

    related_ids = set()
    for mid in memory_ids:
        mem = db.get_memory(mid)
        if mem:
            try:
                links = json.loads(mem["related_to"] or "[]")
                for lid in links:
                    if lid not in memory_ids:
                        related_ids.add(lid)
            except (json.JSONDecodeError, TypeError):
                continue

    if not related_ids:
        return []

    # Fetch related memories
    results = []
    for rid in list(related_ids)[:limit * 2]:
        mem = db.get_memory(rid)
        if mem and mem["is_active"]:
            results.append(dict(mem))

    # Sort by importance
    results.sort(key=lambda x: x.get("importance", 5), reverse=True)
    return results[:limit]


# ── Reasoning Chain Search ──────────────────────────────────────

def search_reasoning_chains(query, project=None, limit=5):
    """Search reasoning chains using FTS and vector similarity.

    Returns chains sorted by relevance with FSRS refresh.
    """
    results = {}

    # 1. FTS search
    fts_results = db.search_reasoning_chains_fts(query, project=project, limit=limit * 2)
    for r in fts_results:
        cid = r["id"]
        results[cid] = {"chain": dict(r), "fts_score": abs(r["rank"])}

    # 2. Vector search for reasoning chains
    model = _get_embedding_model()
    if model is not None:
        conn = db.connect()
        rows = conn.execute("""
            SELECT id, embedding FROM reasoning_chains
            WHERE project=? AND is_active=1 AND embedding IS NOT NULL
        """, (project,)).fetchall() if project else conn.execute("""
            SELECT id, embedding FROM reasoning_chains
            WHERE is_active=1 AND embedding IS NOT NULL
        """).fetchall()

        query_emb = compute_embedding(query)
        if query_emb:
            import numpy as np
            q_vec = np.frombuffer(query_emb, dtype=np.float32)
            for row in rows:
                try:
                    c_vec = np.frombuffer(row["embedding"], dtype=np.float32)
                    score = float(np.dot(q_vec, c_vec))
                    cid = row["id"]
                    if cid in results:
                        results[cid]["vector_score"] = score
                    else:
                        chain = db.get_reasoning_chain(cid)
                        if chain:
                            results[cid] = {"chain": dict(chain), "vector_score": score}
                except Exception:
                    continue

    # 3. Score and sort
    for cid, data in results.items():
        fts = data.get("fts_score", 0.0)
        vec = data.get("vector_score", 0.0)
        relevance = fts * 0.4 + vec * 0.6 if vec > 0 else fts * 0.4

        chain = data["chain"]
        recency = _recency_score(chain.get("last_accessed_at"))
        importance = _importance_score(chain.get("importance", 5))

        data["score"] = (ALPHA_RECENCY * recency +
                         BETA_IMPORTANCE * importance +
                         GAMMA_RELEVANCE * relevance)

    sorted_results = sorted(results.values(), key=lambda x: x["score"], reverse=True)

    # Refresh access for returned chains
    for r in sorted_results[:limit]:
        cid = r["chain"]["id"]
        db.refresh_reasoning_chain(cid)

    return [r["chain"] for r in sorted_results[:limit]]


def format_reasoning_chains_for_context(chains, max_chars=500):
    """Format reasoning chains into injectable context string."""
    if not chains:
        return ""

    lines = []
    total = 0
    for chain in chains:
        steps = json.loads(chain.get("steps") or "[]")
        outcome = chain.get("outcome", "pending")
        mode = chain.get("thinking_mode", "cot")
        question = (chain.get("question") or "")[:80]
        summary = (chain.get("outcome_summary") or "")[:100]

        entry = f"[{mode}|{outcome}] {question} → {summary}"
        if total + len(entry) > max_chars:
            break
        lines.append(entry)
        total += len(entry)

    return "\n".join(lines)

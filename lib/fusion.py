"""Fusion engine: contradiction detection with temporal versioning, memory merging, knowledge evolution.

When a new memory contradicts an older one, the older memory is NOT deleted —
instead the new memory gets a `supersedes` pointer to the old one, preserving
the full temporal chain for auditing.
"""
import json
from lib import db, llm, logger, retriever

_log = logger.get()


def run_fusion_cycle(project=None):
    """Run a complete fusion cycle: detect contradictions, merge similar memories, evolve knowledge."""
    stats = {"contradictions": 0, "merged": 0, "evolved": 0, "modules_updated": 0, "errors": 0}
    projects = _get_active_projects(project)
    for proj in projects:
        try:
            stats["contradictions"] += _detect_contradictions(proj)
            stats["merged"] += _merge_similar(proj)
            stats["evolved"] += _evolve_knowledge(proj)
            # Recompute module embeddings after fusion (merges/deactivations change module composition)
            retriever.update_all_module_embeddings(proj)
            stats["modules_updated"] += 1
        except Exception as e:
            _log.error(f"Fusion error for project {proj}: {e}")
            stats["errors"] += 1
    _log.info(f"Fusion cycle complete: {stats}")
    return stats


def _get_active_projects(project=None):
    if project:
        return [project]
    conn = db.connect()
    rows = conn.execute(
        "SELECT DISTINCT project FROM memories WHERE is_active=1"
    ).fetchall()
    return [r["project"] for r in rows]


def _detect_contradictions(project):
    """Find contradictory memories and mark supersedes chain.

    Instead of deactivating old memories, the new memory gets a `supersedes`
    pointer to the old one. The old memory stays in the database as history.
    """
    conn = db.connect()
    recent = conn.execute("""
        SELECT id, title, narrative, facts, category, obs_type, created_at
        FROM memories WHERE project=? AND is_active=1 AND category='observation'
        ORDER BY created_at DESC LIMIT 20
    """, (project,)).fetchall()

    if len(recent) < 2:
        return 0

    client = llm.get()
    count = 0
    for i, new_mem in enumerate(recent):
        # Skip if already has a supersedes pointer (already resolved)
        existing_supersedes = conn.execute(
            "SELECT supersedes FROM memories WHERE id=?", (new_mem["id"],)
        ).fetchone()
        if existing_supersedes and existing_supersedes["supersedes"]:
            continue

        older = conn.execute("""
            SELECT id, title, narrative, facts, created_at
            FROM memories WHERE project=? AND is_active=1 AND category='observation'
            AND id != ? AND created_at < ? AND supersedes IS NULL
            ORDER BY created_at DESC LIMIT 10
        """, (project, new_mem["id"], new_mem["created_at"])).fetchall()

        if not older:
            continue

        prompt = f"""比较这些记忆是否有矛盾。如果新的记忆取代了旧的，返回旧记忆的 ID。

新记忆 (id={new_mem['id']}): {new_mem['title']} - {new_mem['narrative'] or ''}
事实: {new_mem['facts']}

较早的记忆:
{json.dumps([{'id': o['id'], 'title': o['title'], 'narrative': o['narrative'] or '', 'facts': o['facts']} for o in older], ensure_ascii=False, indent=1)}

返回 JSON: {{"contradictions": [{{"old_id": <id>, "reason": "<被取代的原因>"}}]}}
如果没有矛盾，返回 {{"contradictions": []}}"""

        try:
            raw = client.chat(
                messages=[{"role": "user", "content": prompt}],
                system="检测矛盾或被取代的记忆。保守判断 - 只标记明确的矛盾。",
                temperature=0.1,
            )
            result = client.extract_json(raw)
            for c in result.get("contradictions", []):
                old_id = c.get("old_id")
                reason = c.get("reason", "superseded by newer information")
                if old_id:
                    # Temporal versioning: set supersedes pointer on the new memory
                    db.update_memory(new_mem["id"], supersedes=old_id)
                    # Deactivate the old memory
                    db.deactivate_memory(old_id, reason=f"Superseded by #{new_mem['id']}: {reason}")
                    db.log_fusion("contradiction",
                                  source_id=new_mem["id"], target_id=old_id,
                                  reason=f"时序版本化: {reason}")
                    count += 1
                    _log.info(f"Temporal versioning: #{new_mem['id']} supersedes #{old_id} - {reason}")
        except Exception as e:
            _log.warning(f"Contradiction detection error: {e}")
    return count


def _merge_similar(project):
    """Find and merge very similar memories."""
    conn = db.connect()
    memories = conn.execute("""
        SELECT id, title, narrative, facts, concepts, category, obs_type
        FROM memories WHERE project=? AND is_active=1
        ORDER BY created_at DESC LIMIT 50
    """, (project,)).fetchall()

    if len(memories) < 2:
        return 0

    client = llm.get()
    count = 0
    title_groups = {}
    for m in memories:
        title = (m["title"] or "").lower().strip()
        if not title:
            continue
        matched = False
        for key in title_groups:
            if _title_similar(title, key):
                title_groups[key].append(m)
                matched = True
                break
        if not matched:
            title_groups[title] = [m]

    for title_key, group in title_groups.items():
        if len(group) < 2:
            continue

        prompt = f"""这些记忆涉及相似主题。将它们合并为一条综合记忆。

待合并的记忆:
{json.dumps([{'id': m['id'], 'title': m['title'], 'narrative': m['narrative'] or '', 'facts': m['facts'], 'concepts': m['concepts']} for m in group], ensure_ascii=False, indent=1)}

返回 JSON:
- "merged_title": 简明标题
- "merged_narrative": 合并后的叙述
- "merged_facts": 去重后的事实数组
- "merged_concepts": 合并后的概念
- "keep_id": 保留的记忆 ID（其他将被停用）

只输出有效 JSON。"""

        try:
            raw = client.chat(
                messages=[{"role": "user", "content": prompt}],
                system="将相似记忆合并为一条综合条目。保留所有独特事实。",
                temperature=0.2,
            )
            result = client.extract_json(raw)
            if result and result.get("keep_id"):
                keep_id = result["keep_id"]
                db.update_memory(
                    keep_id,
                    title=result.get("merged_title"),
                    narrative=result.get("merged_narrative"),
                    facts=json.dumps(result.get("merged_facts", []), ensure_ascii=False),
                    concepts=json.dumps(result.get("merged_concepts", []), ensure_ascii=False),
                )
                for m in group:
                    if m["id"] != keep_id:
                        db.deactivate_memory(m["id"], reason="merged")
                        db.log_fusion("merge", source_id=m["id"], target_id=keep_id, reason="similar content")
                        count += 1
                _log.info(f"Merged {len(group)} similar memories into #{keep_id}")
        except Exception as e:
            _log.warning(f"Merge error: {e}")
    return count


def _evolve_knowledge(project):
    """Extract and update persistent knowledge from observations."""
    conn = db.connect()
    recent = conn.execute("""
        SELECT id, title, narrative, facts, concepts, category
        FROM memories WHERE project=? AND is_active=1
        AND category IN ('user', 'reference', 'feedback', 'project')
        AND id NOT IN (SELECT COALESCE(memory_id, 0) FROM knowledge_index WHERE project=?)
        ORDER BY created_at DESC LIMIT 20
    """, (project, project)).fetchall()

    count = 0
    for mem in recent:
        facts = []
        try:
            facts = json.loads(mem["facts"]) if mem["facts"] else []
        except (json.JSONDecodeError, TypeError):
            pass

        for fact in facts:
            if not fact or len(fact) < 5:
                continue
            key = _fact_to_key(fact)
            existing = db.get_knowledge(project, key)
            if existing:
                if existing["value"] != fact:
                    db.set_knowledge(project, key, fact, memory_id=mem["id"])
                    count += 1
            else:
                db.set_knowledge(project, key, fact, memory_id=mem["id"])
                count += 1
        if facts:
            db.log_fusion("evolve", source_id=mem["id"], reason=f"Extracted {len(facts)} knowledge entries")
    return count


def _title_similar(a, b):
    """Simple title similarity check."""
    a_words = set(a.split())
    b_words = set(b.split())
    if not a_words or not b_words:
        return False
    overlap = len(a_words & b_words)
    return overlap / min(len(a_words), len(b_words)) > 0.6


def _fact_to_key(fact):
    """Convert a fact string to a knowledge index key."""
    key = fact[:50].lower().strip()
    key = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in key)
    return key.strip("_") or "misc"

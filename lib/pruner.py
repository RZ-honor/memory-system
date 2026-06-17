"""记忆剪枝：FSRS 衰减、过期清理、去重、质量过滤、模式泛化、渐进式遗忘

FSRS (Free Spaced Repetition Scheduler) decay model:
  R(t) = (1 + t/(9*S))^(-1)
where R is retention, t is time since last review, S is stability.
R < 0.1 marks memory for cleanup.

渐进式遗忘机制：
- 访问强化：被检索/引用的记忆重要性提升
- 矛盾淘汰：被新记忆标记为过时的自动降权
- 验证衰减：长期未被验证的推测性记忆加速衰减
"""
import json, time, math
from lib import db, llm, logger

_log = logger.get()

# FSRS parameters
FSRS_DECAY_FACTOR = 9.0  # Standard FSRS decay factor
RETENTION_THRESHOLD = 0.1  # Below this, mark for cleanup
STABILITY_BOOST_ON_ACCESS = 1.3  # Multiplier when memory is accessed

# Progressive forgetting parameters
ACCESS_BOOST = 1.2  # Importance boost per access
SUPERSEDED_DECAY = 0.5  # Importance multiplier for superseded memories
UNVERIFIED_DECAY_RATE = 0.05  # Daily decay for unverified memories
VERIFICATION_KEYWORDS = ["验证", "确认", "测试通过", "有效", "解决", "verified", "confirmed", "tested"]


def run_pruning_cycle(project=None):
    """Run complete pruning cycle."""
    stats = {"expired": 0, "decayed": 0, "deduped": 0, "generalized": 0, "low_quality": 0,
             "access_boosted": 0, "superseded_decayed": 0, "unverified_decayed": 0}
    projects = _get_active_projects(project)
    for proj in projects:
        try:
            stats["expired"] += _expire_stale(proj)
            stats["decayed"] += _apply_fsrs_decay(proj)
            stats["deduped"] += _deduplicate(proj)
            stats["generalized"] += _generalize_patterns(proj)
            stats["low_quality"] += _remove_low_quality(proj)
            stats["access_boosted"] += _apply_access_boost(proj)
            stats["superseded_decayed"] += _apply_superseded_decay(proj)
            stats["unverified_decayed"] += _apply_unverified_decay(proj)
        except Exception as e:
            _log.error(f"剪枝错误 [{proj}]: {e}")
    _log.info(f"剪枝周期完成: {stats}")
    return stats


def _get_active_projects(project=None):
    if project:
        return [project]
    conn = db.connect()
    rows = conn.execute("SELECT DISTINCT project FROM memories WHERE is_active=1").fetchall()
    return [r["project"] for r in rows]


def _fsrs_retention(stability, hours_since_review):
    """Calculate FSRS retention: R(t) = (1 + t/(9*S))^(-1)"""
    if stability <= 0:
        stability = 0.1
    return (1 + hours_since_review / (FSRS_DECAY_FACTOR * stability)) ** -1


def _expire_stale(project):
    """Deactivate memories with very low FSRS retention."""
    conn = db.connect()
    now = time.time()
    rows = conn.execute("""
        SELECT id, title, last_accessed_at, created_at, stability, metadata
        FROM memories
        WHERE project=? AND is_active=1 AND category='observation'
    """, (project,)).fetchall()
    count = 0
    for r in rows:
        # Calculate time since last access (or creation if never accessed)
        ref_time_str = r["last_accessed_at"] or r["created_at"]
        if not ref_time_str:
            continue
        try:
            ref_time = time.mktime(time.strptime(ref_time_str[:19], "%Y-%m-%dT%H:%M:%S"))
        except (ValueError, TypeError):
            continue
        hours = (now - ref_time) / 3600.0
        stability = r["stability"] or 1.0
        retention = _fsrs_retention(stability, hours)
        if retention < RETENTION_THRESHOLD:
            db.deactivate_memory(r["id"], reason=f"FSRS retention={retention:.3f} < {RETENTION_THRESHOLD}")
            db.log_fusion("expire", source_id=r["id"],
                          reason=f"FSRS衰减: retention={retention:.3f}, stability={stability:.2f}, hours={hours:.0f}")
            count += 1
    return count


def _apply_fsrs_decay(project):
    """Update metadata with current FSRS retention for monitoring."""
    conn = db.connect()
    now = time.time()
    rows = conn.execute("""
        SELECT id, last_accessed_at, created_at, stability, metadata
        FROM memories
        WHERE project=? AND is_active=1 AND category='observation'
    """, (project,)).fetchall()
    count = 0
    for r in rows:
        ref_time_str = r["last_accessed_at"] or r["created_at"]
        if not ref_time_str:
            continue
        try:
            ref_time = time.mktime(time.strptime(ref_time_str[:19], "%Y-%m-%dT%H:%M:%S"))
        except (ValueError, TypeError):
            continue
        hours = (now - ref_time) / 3600.0
        stability = r["stability"] or 1.0
        retention = _fsrs_retention(stability, hours)
        # Update metadata with retention info for monitoring
        meta = {}
        try:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
        except (json.JSONDecodeError, TypeError):
            pass
        meta["retention"] = round(retention, 3)
        meta["stability"] = round(stability, 3)
        db.update_memory(r["id"], metadata=json.dumps(meta, ensure_ascii=False))
        count += 1
    return count


def _deduplicate(project):
    """Remove highly duplicate memories."""
    conn = db.connect()
    rows = conn.execute("""
        SELECT id, title, narrative, facts, content_hash FROM memories
        WHERE project=? AND is_active=1
        ORDER BY created_at DESC
    """, (project,)).fetchall()
    seen_hashes = {}
    count = 0
    for r in rows:
        h = r["content_hash"]
        if h in seen_hashes:
            db.deactivate_memory(r["id"], reason="重复内容")
            db.log_fusion("dedup", source_id=seen_hashes[h], target_id=r["id"], reason="哈希去重")
            count += 1
        else:
            seen_hashes[h] = r["id"]
    return count


def _generalize_patterns(project):
    """Extract generalized knowledge from repeated problem-solving patterns."""
    conn = db.connect()
    rows = conn.execute("""
        SELECT id, title, narrative, facts, concepts, obs_type FROM memories
        WHERE project=? AND is_active=1 AND obs_type IN ('bugfix', 'discovery')
        ORDER BY created_at DESC LIMIT 50
    """, (project,)).fetchall()
    if len(rows) < 3:
        return 0

    client = llm.get()
    groups = _group_by_similarity(rows)
    count = 0
    for group_key, members in groups.items():
        if len(members) < 2:
            continue
        prompt = f"""分析这些类似的问题解决记录，提取可泛化的模式和最佳实践。

记录:
{json.dumps([{'title': m['title'], 'narrative': m['narrative'] or '', 'facts': json.loads(m['facts']) if m['facts'] else []} for m in members[:5]], ensure_ascii=False, indent=1)}

返回 JSON:
- "pattern_name": 模式名称
- "pattern_description": 模式描述（通用解决方案）
- "key_steps": 关键步骤列表
- "common_pitfalls": 常见陷阱
- "applicable_contexts": 适用场景

只输出有效 JSON。"""

        try:
            raw = client.chat(
                messages=[{"role": "user", "content": prompt}],
                system="从具体案例中提取通用模式和最佳实践。用中文回答。",
                temperature=0.2,
            )
            result = client.extract_json(raw)
            if result and result.get("pattern_name"):
                db.insert_memory(
                    project=project,
                    category="reference",
                    obs_type="decision",
                    memory_type="procedural",
                    title=f"模式: {result['pattern_name']}",
                    narrative=result.get("pattern_description"),
                    facts=result.get("key_steps", []),
                    concepts=["泛化模式", "最佳实践"] + result.get("applicable_contexts", []),
                    content=json.dumps(result, ensure_ascii=False),
                    importance=6,
                )
                count += 1
                _log.info(f"提取泛化模式: {result['pattern_name']}")
        except Exception as e:
            _log.warning(f"模式泛化错误: {e}")
    return count


def _remove_low_quality(project):
    """Remove low-quality memories: quality<3 or noise patterns."""
    conn = db.connect()
    rows = conn.execute("""
        SELECT id, title, metadata, narrative FROM memories
        WHERE project=? AND is_active=1 AND category='observation'
    """, (project,)).fetchall()
    count = 0
    noise_keywords = [
        "favicon", "控制台错误", "console error", "404",
        "页面标题", "快照", "snapshot", "screenshot",
        "截图", "playwright", "browser", "浏览器",
        "页面访问", "控制台报告", "页面加载",
        "browser_navigate", "browser_snapshot", "browser_take",
    ]
    for r in rows:
        meta = {}
        try:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
        except (json.JSONDecodeError, TypeError):
            pass
        quality = meta.get("quality", 0)
        title = (r["title"] or "").lower()
        narrative = (r["narrative"] or "").lower()
        combined = title + " " + narrative
        is_noise = any(p in combined for p in noise_keywords)
        if (quality > 0 and quality < 3) or (is_noise and quality < 4):
            db.deactivate_memory(r["id"], reason=f"低质量/噪音 (quality={quality})")
            db.log_fusion("prune_quality", source_id=r["id"], reason=f"质量分={quality}, 噪音匹配")
            count += 1
    return count


def _group_by_similarity(rows):
    """Group rows by title similarity."""
    groups = {}
    for r in rows:
        title = (r["title"] or "").lower().strip()
        if not title:
            continue
        matched = False
        for key in groups:
            if _title_similar(title, key):
                groups[key].append(r)
                matched = True
                break
        if not matched:
            groups[title] = [r]
    return groups


def _title_similar(a, b):
    """Simple title similarity check."""
    a_words = set(a.split())
    b_words = set(b.split())
    if not a_words or not b_words:
        return False
    overlap = len(a_words & b_words)
    return overlap / min(len(a_words), len(b_words)) > 0.5


# ── Progressive Forgetting Mechanisms ────────────────────────────

def _apply_access_boost(project):
    """Boost importance for memories that have been accessed recently.

    Memories that are retrieved or referenced should be reinforced.
    """
    conn = db.connect()
    # Find memories accessed in the last 7 days
    rows = conn.execute("""
        SELECT id, importance, access_count FROM memories
        WHERE project=? AND is_active=1 AND access_count > 0
        AND last_accessed_at > datetime('now', '-7 days')
    """, (project,)).fetchall()

    count = 0
    for r in rows:
        old_importance = r["importance"] or 5
        # Boost based on access count (diminishing returns)
        boost = min(ACCESS_BOOST ** min(r["access_count"], 5), 1.5)
        new_importance = min(10, int(old_importance * boost))
        if new_importance > old_importance:
            db.update_memory(r["id"], importance=new_importance)
            count += 1

    return count


def _apply_superseded_decay(project):
    """Decay importance for memories that have been superseded.

    When a new memory supersedes an old one, the old one should lose importance.
    """
    conn = db.connect()
    # Find memories that have been superseded (have supersedes link)
    rows = conn.execute("""
        SELECT id, importance FROM memories
        WHERE project=? AND is_active=1 AND supersedes IS NOT NULL AND supersedes != ''
    """, (project,)).fetchall()

    count = 0
    for r in rows:
        old_importance = r["importance"] or 5
        new_importance = max(1, int(old_importance * SUPERSEDED_DECAY))
        if new_importance < old_importance:
            db.update_memory(r["id"], importance=new_importance)
            count += 1

    return count


def _apply_unverified_decay(project):
    """Decay importance for unverified memories over time.

    Memories that contain speculative information and haven't been verified
    should gradually lose importance.
    """
    conn = db.connect()
    # Find memories without verification keywords in their narrative
    rows = conn.execute("""
        SELECT id, importance, created_at, narrative FROM memories
        WHERE project=? AND is_active=1 AND category='observation'
    """, (project,)).fetchall()

    count = 0
    for r in rows:
        narrative = (r["narrative"] or "").lower()
        # Check if memory contains verification keywords
        is_verified = any(kw in narrative for kw in VERIFICATION_KEYWORDS)
        if is_verified:
            continue

        # Calculate age in days
        try:
            created = r["created_at"]
            if created:
                from datetime import datetime
                if isinstance(created, str):
                    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                else:
                    created_dt = created
                age_days = (datetime.now() - created_dt.replace(tzinfo=None)).days
            else:
                age_days = 0
        except:
            age_days = 0

        if age_days < 7:
            continue  # Don't decay very new memories

        old_importance = r["importance"] or 5
        # Apply daily decay
        decay_factor = (1 - UNVERIFIED_DECAY_RATE) ** min(age_days, 30)
        new_importance = max(1, int(old_importance * decay_factor))
        if new_importance < old_importance:
            db.update_memory(r["id"], importance=new_importance)
            count += 1

    return count

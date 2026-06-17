"""MCP Server for memory system — exposes three-factor retrieval, injection, and management as MCP tools.

Run with:
    D:/MINICONDA/envs/memory-system/python.exe D:/claude/memory-system/mcp_server.py

Registers as stdio MCP server in Claude Code settings.json.
"""
import sys, os, json, threading, time

# Ensure lib is importable
sys.path.insert(0, os.path.dirname(__file__))

from mcp.server.fastmcp import FastMCP
from lib import db, config, retriever, logger, observer, fusion, pruner

_log = logger.get()

# Initialize DB on startup
db.connect()


def _process_pending_on_startup():
    """Process pending retry items when MCP server starts.

    This ensures that items that failed in previous sessions are retried
    when Claude Code is reopened.
    """
    try:
        # Check for pending items
        stats = db.queue_stats()
        pending_count = sum(r["cnt"] for r in stats if r["status"] in ("pending", "retry"))

        if pending_count > 0:
            _log.info(f"发现 {pending_count} 条待处理队列项，开始处理...")

            # Process retryable items
            retryable = db.dequeue_retryable(limit=20)
            success = 0
            for item in retryable:
                try:
                    _process_queue_item(item)
                    db.mark_processed(item["id"])
                    success += 1
                except Exception as e:
                    _log.warning(f"启动重试失败 #{item['id']}: {e}")
                    db.mark_failed(item["id"], str(e))

            if success > 0:
                _log.info(f"启动重试完成: {success}/{len(retryable)} 条成功处理")

            # Also process pending items
            pending = db.dequeue(limit=10)
            for item in pending:
                try:
                    _process_queue_item(item)
                    db.mark_processed(item["id"])
                    success += 1
                except Exception as e:
                    _log.warning(f"启动处理失败 #{item['id']}: {e}")
                    db.mark_failed(item["id"], str(e))

            return {"processed": success, "total": pending_count}
        else:
            _log.info("队列为空，无需处理")
            return {"processed": 0, "total": 0}
    except Exception as e:
        _log.error(f"启动处理队列错误: {e}")
        return {"error": str(e)}


def _process_queue_item(item):
    """Process a single queue item. Raises on LLM/network failures."""
    event = item["hook_event"]
    project = item["project"]
    session = item["session_uuid"]

    if event == "post_tool_use":
        tool_name = item["tool_name"]
        tool_input = _safe_json(item["tool_input"])
        tool_response = _safe_json(item["tool_response"])
        observer.process_interaction(
            project=project,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_response=tool_response,
            session_uuid=session,
        )
    elif event == "session_end":
        extra = _safe_json(item["extra"], default={}) or {}
        interactions = extra.get("interactions", [])
        if interactions:
            saved = observer.process_batch(project, interactions, session_uuid=session)
            _log.info(f"SessionEnd [{session}]: saved {saved} observations")
            observer.process_reflection(project, session, interactions)
            # Extract solution-oriented memories
            try:
                solution_saved = observer.process_solution_extraction(project, session, interactions)
                if solution_saved:
                    _log.info(f"SessionEnd [{session}]: extracted {solution_saved} solution memories")
            except Exception as e:
                _log.warning(f"Solution extraction failed (non-blocking): {e}")
    else:
        _log.debug(f"未处理的队列事件: {event}")


def _safe_json(text, default=None):
    if not text:
        return default if default is not None else ""
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


# Process pending items on startup
_startup_result = _process_pending_on_startup()

mcp = FastMCP(
    "memory-system",
    instructions="""Persistent memory system with three-factor retrieval (recency + importance + relevance),
FSRS decay, and knowledge fusion.

CRITICAL WORKFLOW:
1. At the START of every conversation, call inject_context with the user's first message
2. Before answering questions about past work, call search_memory first
3. After completing significant work, call save_memory to record learnings
4. At session end, call run_maintenance to clean up memories""",
)


# ── Tools ────────────────────────────────────────────────────────

@mcp.tool()
def search_memory(query: str, project: str = None, limit: int = 5) -> str:
    """Search memories using three-factor retrieval (recency + importance + relevance).

    Use this when you need to recall past interactions, decisions, bugs, or knowledge.
    Returns memories sorted by combined score: recent + important + relevant.

    Args:
        query: Search query (natural language)
        project: Optional project filter (e.g. "PROJECT", "VOICE_voice_split")
        limit: Max results (default 5)
    """
    results = retriever.search(query, project=project, limit=limit, use_vector=True)
    if not results:
        return "No memories found."

    lines = []
    for i, m in enumerate(results, 1):
        title = m.get("title", "")
        narrative = m.get("narrative", "")
        cat = m.get("category", "")
        mtype = m.get("memory_type", "")
        importance = m.get("importance", 5)
        proj = m.get("project", "")

        entry = f"{i}. [{cat}/{mtype}] {title}"
        if importance >= 8:
            entry += " ★"
        if narrative:
            entry += f"\n   {narrative[:200]}"
        entry += f"\n   project={proj} importance={importance}"
        lines.append(entry)

    return "\n\n".join(lines)


@mcp.tool()
def save_memory(
    title: str,
    project: str,
    narrative: str = "",
    category: str = "observation",
    memory_type: str = "semantic",
    importance: int = 6,
    facts: list[str] = None,
    concepts: list[str] = None,
    obs_type: str = None,
) -> str:
    """Save a new memory to the persistent store.

    Use this to record important decisions, discoveries, user preferences,
    bug fixes, or lessons learned for future reference.

    Args:
        title: Short title (60 chars max)
        project: Project identifier (e.g. "PROJECT", "VOICE_voice_split")
        narrative: 1-3 sentence summary
        category: "observation", "user", "reference", "feedback", "project"
        memory_type: "episodic" (events), "semantic" (facts), "procedural" (how-to), "reflective" (lessons)
        importance: 1-10 (10=critical, 1=trivial). Default 6.
        facts: List of specific facts to remember
        concepts: Tags/labels
        obs_type: "discovery", "bugfix", "change", "decision", "feature", "refactor", "security_note"
    """
    facts = facts or []
    concepts = concepts or []

    mem_id = db.insert_memory(
        project=project,
        category=category,
        obs_type=obs_type,
        memory_type=memory_type,
        title=title,
        narrative=narrative,
        facts=facts,
        concepts=concepts,
        content=narrative or title,
        importance=importance,
        metadata=json.dumps({"quality": min(importance, 5)}),
    )

    # Update embedding in background
    text = f"{title} {narrative} {' '.join(facts)}"
    try:
        retriever.update_embedding(mem_id, text)
    except Exception as e:
        _log.warning(f"Embedding update failed for #{mem_id}: {e}")

    return f"Memory saved: id={mem_id} title={title}"


@mcp.tool()
def inject_context(message: str, project: str = None, max_chars: int = 1000, message_count: int = 1) -> str:
    """MANDATORY: Call this at the START of every conversation turn to inject relevant past knowledge.

    This tool MUST be called:
    1. At the beginning of every new conversation
    2. When the user asks about past work, decisions, or history
    3. Before answering questions that might benefit from past context

    Uses progressive retrieval strategy:
    - message_count=1 (first message): Fast keyword search, top 3 results
    - message_count=2-3: Full three-factor search, top 5 results
    - message_count=4+: On-demand search, only when relevant

    Args:
        message: The user's current message or question
        project: Optional project filter
        max_chars: Max character count for context (default 1000)
        message_count: Number of messages in current session (for progressive retrieval)
    """
    # Progressive retrieval strategy
    if message_count == 1:
        # Phase 1: Fast keyword search, no embedding needed
        keywords = retriever.extract_keywords(message)
        results = db.search_by_keywords(keywords, project=project, limit=3)
        search_type = "keyword"
    elif message_count <= 3:
        # Phase 2: Full three-factor search
        results = retriever.search(message, project=project, limit=5, use_vector=True)
        search_type = "three-factor"
    else:
        # Phase 3: On-demand only (still search but with lower limit)
        results = retriever.search(message, project=project, limit=3, use_vector=True)
        search_type = "on-demand"

    if not results:
        return ""

    lines = [f"[Memory System] Found {len(results)} relevant memories ({search_type}):"]
    total = len(lines[0])

    for r in results:
        title = r.get("title", "")
        narrative = r.get("narrative", "")
        proj = r.get("project", "")
        cat = r.get("category", "")
        importance = r.get("importance", 5)

        entry = f"  - [{proj}/{cat}] {title}"
        if importance >= 8:
            entry += " ★"
        if narrative:
            entry += f": {narrative[:150]}"

        if total + len(entry) > max_chars:
            break
        lines.append(entry)
        total += len(entry)

    # Also check for matching module and its reasoning chains
    module_id, module_name, score = retriever.match_module(message, project=project)
    if module_id and score >= 0.5:  # Increased threshold from 0.3 to 0.5
        chains = db.list_reasoning_chains(module_id=module_id, limit=2)
        if chains:
            chain_line = f"  Related reasoning ({len(chains)} chains):"
            if total + len(chain_line) <= max_chars:
                lines.append(chain_line)
                for c in chains:
                    q = (c["question"] or "")[:60]
                    outcome = c["outcome"]
                    cl = f"    [{outcome}] {q}"
                    if total + len(cl) <= max_chars:
                        lines.append(cl)
                        total += len(cl)

    return "\n".join(lines)


@mcp.tool()
def search_with_web_fallback(query: str, project: str = None, web_search_results: str = None) -> str:
    """Search memories with optional web search context integration.

    Use this when you need to combine past knowledge with current web search results.
    This helps balance historical context with up-to-date information.

    Args:
        query: Search query
        project: Optional project filter
        web_search_results: Optional web search results to integrate with memory search
    """
    # Search memories
    memory_results = retriever.search(query, project=project, limit=3, use_vector=True)

    lines = []

    if memory_results:
        lines.append("=== Memory Search Results ===")
        for i, m in enumerate(memory_results, 1):
            title = m.get("title", "")
            narrative = m.get("narrative", "")
            importance = m.get("importance", 5)

            entry = f"{i}. {title}"
            if importance >= 8:
                entry += " ★"
            if narrative:
                entry += f"\n   {narrative[:150]}"
            lines.append(entry)

    if web_search_results:
        lines.append("\n=== Web Search Results ===")
        lines.append(web_search_results[:500])  # Limit web results

    # Provide balance recommendation
    if memory_results and web_search_results:
        lines.append("\n=== Context Balance ===")
        lines.append("Use memory results for historical context and decisions.")
        lines.append("Use web results for current information and best practices.")
        lines.append("Combine both for comprehensive answers.")
    elif memory_results:
        lines.append("\n=== Recommendation ===")
        lines.append("Found relevant memories. Use these for historical context.")
    elif web_search_results:
        lines.append("\n=== Recommendation ===")
        lines.append("No relevant memories found. Rely on web search results.")

    return "\n".join(lines)


@mcp.tool()
def list_modules(project: str = None) -> str:
    """List memory modules (topic clusters) with their descriptions and memory counts.

    Args:
        project: Optional project filter
    """
    modules = db.get_modules(project)
    if not modules:
        return "No modules found."

    lines = []
    for m in modules:
        desc = m.get("description", "") or "no description"
        count = m.get("memory_count", 0)
        proj = m.get("project", "")
        lines.append(f"- {m['name']} ({count} memories) [{proj}]: {desc}")

    return "\n".join(lines)


@mcp.tool()
def get_stats(project: str = None) -> str:
    """Get memory system statistics: total memories, modules, sessions, queue status.

    Args:
        project: Optional project filter
    """
    stats = db.stats()

    # Add project-specific count if filtered
    if project:
        conn = db.connect()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM memories WHERE project=? AND is_active=1",
            (project,)
        ).fetchone()
        stats[f"active_in_{project}"] = row["cnt"] if row else 0

    return json.dumps(stats, indent=2, ensure_ascii=False)


@mcp.tool()
def search_reasoning(query: str, project: str = None, limit: int = 3) -> str:
    """Search reasoning chains (structured problem-solving records).

    Use this to find how past problems were solved, what approaches were tried,
    and what conclusions were reached.

    Args:
        query: Search query
        project: Optional project filter
        limit: Max results (default 3)
    """
    chains = retriever.search_reasoning_chains(query, project=project, limit=limit)
    if not chains:
        return "No reasoning chains found."

    lines = []
    for i, c in enumerate(chains, 1):
        question = c.get("question", "")
        outcome = c.get("outcome", "pending")
        summary = c.get("outcome_summary", "")
        mode = c.get("thinking_mode", "cot")
        importance = c.get("importance", 5)

        steps = []
        try:
            steps = json.loads(c.get("steps") or "[]")
        except (json.JSONDecodeError, TypeError):
            pass

        entry = f"{i}. [{mode}|{outcome}] {question}"
        if importance >= 7:
            entry += " ★"
        if summary:
            entry += f"\n   Result: {summary[:200]}"
        if steps:
            entry += f"\n   Steps: {len(steps)}"
        lines.append(entry)

    return "\n\n".join(lines)


@mcp.tool()
def run_maintenance(project: str = None) -> str:
    """Run memory maintenance: fusion (contradiction detection + merging) and pruning (FSRS decay + cleanup).

    Use this periodically to keep the memory store clean and consistent.
    Typically called at session end or when memories feel stale.

    Args:
        project: Optional project filter
    """
    # Run fusion
    fusion_stats = fusion.run_fusion_cycle(project=project)

    # Run pruning
    prune_stats = pruner.run_pruning_cycle(project=project)

    result = {
        "fusion": fusion_stats,
        "pruning": prune_stats,
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
def extract_from_session(project: str, interactions: list[dict]) -> str:
    """Extract structured memories from a batch of tool interactions.

    Use this to process a session's worth of interactions and extract
    high-quality observations, decisions, and lessons.

    Args:
        project: Project identifier
        interactions: List of dicts with keys: tool_name, tool_input, tool_response
    """
    if not interactions:
        return "No interactions to process."

    saved = observer.process_batch(
        project=project,
        interactions=interactions[:30],  # Cap at 30
        context="MCP batch extraction",
    )

    return f"Extracted {saved} memories from {len(interactions)} interactions."


@mcp.tool()
def process_pending_queue(limit: int = 10) -> str:
    """Process pending items in the memory queue.

    Use this when there are unprocessed memories from previous sessions.
    This automatically retries failed items and processes new ones.

    Args:
        limit: Max items to process (default 10)
    """
    try:
        # Process retryable items first
        retryable = db.dequeue_retryable(limit=limit)
        success = 0
        failed = 0

        for item in retryable:
            try:
                _process_queue_item(item)
                db.mark_processed(item["id"])
                success += 1
            except Exception as e:
                _log.warning(f"重试失败 #{item['id']}: {e}")
                db.mark_failed(item["id"], str(e))
                failed += 1

        # Then process pending items
        pending = db.dequeue(limit=limit - success)
        for item in pending:
            try:
                _process_queue_item(item)
                db.mark_processed(item["id"])
                success += 1
            except Exception as e:
                _log.warning(f"处理失败 #{item['id']}: {e}")
                db.mark_failed(item["id"], str(e))
                failed += 1

        # Get final queue stats
        stats = db.queue_stats()
        queue_status = {r["status"]: r["cnt"] for r in stats}

        return json.dumps({
            "processed": success,
            "failed": failed,
            "queue_status": queue_status,
        }, indent=2, ensure_ascii=False)

    except Exception as e:
        return f"Error processing queue: {str(e)}"


@mcp.tool()
def get_queue_status() -> str:
    """Get the current status of the memory processing queue.

    Use this to check if there are pending items that need processing.
    """
    try:
        stats = db.queue_stats()
        queue_status = {r["status"]: r["cnt"] for r in stats}

        # Get recent failed items for debugging
        conn = db.connect()
        failed_items = conn.execute("""
            SELECT id, hook_event, project, last_error, retry_count, created_at
            FROM pending_queue
            WHERE status IN ('failed', 'retry')
            ORDER BY created_at DESC LIMIT 5
        """).fetchall()

        result = {
            "queue_status": queue_status,
            "recent_failed": [dict(item) for item in failed_items] if failed_items else [],
        }

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        return f"Error getting queue status: {str(e)}"


# ── Resources ────────────────────────────────────────────────────

@mcp.resource("memory://stats")
def memory_stats() -> str:
    """Current memory system statistics."""
    stats = db.stats()
    return json.dumps(stats, indent=2, ensure_ascii=False)


@mcp.resource("memory://config")
def memory_config() -> str:
    """Current memory system configuration (sanitized)."""
    cfg = config.load()
    safe = json.loads(json.dumps(cfg))
    if "llm" in safe and safe["llm"].get("api_key"):
        key = safe["llm"]["api_key"]
        safe["llm"]["api_key"] = "•" * (len(key) - 4) + key[-4:] if len(key) > 4 else "•" * len(key)
    return json.dumps(safe, indent=2, ensure_ascii=False)


# ── Prompts ──────────────────────────────────────────────────────

@mcp.prompt()
def recall_memories(topic: str, project: str = "") -> str:
    """Generate a prompt to search and recall memories about a topic."""
    proj_filter = f" in project '{project}'" if project else ""
    return f"""Search the memory system for information about: {topic}{proj_filter}

Use the search_memory tool to find relevant past memories, then summarize what you find.
If no memories are found, say so — don't fabricate information."""


@mcp.prompt()
def save_learnings(project: str) -> str:
    """Generate a prompt to save important learnings from the current session."""
    return f"""Review what was accomplished in this session and save important learnings to the memory system.

Use save_memory to record:
- Key decisions made and why
- Bugs found and how they were fixed
- User preferences or requirements expressed
- Lessons learned or pitfalls discovered
- Architecture decisions

Project: {project}
Only save genuinely important information. Quality over quantity."""


# ── Main ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    _log.info("Starting Memory System MCP Server...")
    mcp.run(transport="stdio")

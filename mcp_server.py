"""MCP Server for memory system — exposes three-factor retrieval, injection, and management as MCP tools.

Run with:
    D:/MINICONDA/envs/memory-system/python.exe D:/claude/memory-system/mcp_server.py

Registers as stdio MCP server in Claude Code settings.json.
"""
import sys, os, json

# Ensure lib is importable
sys.path.insert(0, os.path.dirname(__file__))

from mcp.server.fastmcp import FastMCP
from lib import db, config, retriever, logger, observer, fusion, pruner

_log = logger.get()

# Initialize DB on startup
db.connect()

mcp = FastMCP(
    "memory-system",
    instructions="Persistent memory system with three-factor retrieval (recency + importance + relevance), FSRS decay, and knowledge fusion.",
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
def inject_context(message: str, project: str = None, max_chars: int = 1000) -> str:
    """Get relevant memory context for the current user message.

    Call this at the START of a conversation turn to inject relevant past knowledge.
    Uses three-factor search to find the most relevant memories for the current context.

    Args:
        message: The user's current message or question
        project: Optional project filter
        max_chars: Max character count for context (default 1000)
    """
    results = retriever.search(message, project=project, limit=5, use_vector=True)
    if not results:
        return ""

    lines = [f"[Memory System] Found {len(results)} relevant memories:"]
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
    if module_id and score >= 0.3:
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

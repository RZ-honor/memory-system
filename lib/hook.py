"""Claude Code hook handler - captures tool interactions and provides minimal index injection.

Strategy: inject only module overview / knowledge index into context.
Model searches on-demand when it needs specific memories or reasoning chains.
"""
import json, sys, os, time, uuid, threading, re
from lib import db, config, retriever, logger

_log = logger.get()

_context_buffer = {}
_buffer_lock = threading.Lock()


def handle_hook_event():
    """Read hook event from stdin (JSON) and process it."""
    cfg = config.get("hook") or {}
    skip_tools = set(cfg.get("skip_tools", []))

    try:
        raw = sys.stdin.read().strip()
        if not raw:
            _log.debug("Hook: empty stdin")
            return
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            depth = 0
            last_valid = -1
            for i, ch in enumerate(raw):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        last_valid = i
                        break
            if last_valid > 0:
                event = json.loads(raw[:last_valid + 1])
            else:
                _log.warning(f"Hook: unfixable JSON ({len(raw)} chars)")
                return
    except (json.JSONDecodeError, IOError) as e:
        _log.warning(f"Hook: failed to parse stdin: {e}")
        return

    # Claude Code hook fields
    hook_type = event.get("hook_type", "") or event.get("hook_event", "")
    session_id = event.get("session_id", "") or event.get("sessionId", "")
    if not session_id:
        session_id = str(uuid.uuid4())[:8]
    cwd = event.get("cwd", "") or os.getcwd()
    project = _resolve_project(cwd)

    tool_name = event.get("tool_name", "") or event.get("toolName", "")
    tool_input = event.get("tool_input", {}) or event.get("toolInput", {})
    tool_response = event.get("tool_response", "") or event.get("toolOutput", "")

    if tool_name in skip_tools:
        return

    _log.info(f"Hook: {hook_type} tool={tool_name} project={project}")

    # Enqueue for async processing
    db.enqueue(
        session_uuid=session_id,
        project=project,
        hook_event=hook_type or "post_tool_use",
        tool_name=tool_name,
        tool_input=tool_input,
        tool_response=tool_response,
        cwd=cwd,
    )
    db.upsert_session(session_id, project)

    # Update context buffer
    with _buffer_lock:
        if session_id not in _context_buffer:
            _context_buffer[session_id] = {"project": project, "interactions": [], "start": time.time()}
        _context_buffer[session_id]["interactions"].append({
            "tool": tool_name,
            "input": _summarize_input(tool_name, tool_input),
            "response_preview": str(tool_response)[:300] if tool_response else "",
            "ts": time.time(),
        })

    # Update session tool count
    conn = db.connect()
    conn.execute("UPDATE sessions SET tool_count = tool_count + 1 WHERE session_uuid=?", (session_id,))
    conn.commit()


def handle_user_prompt_submit():
    """Handle UserPromptSubmit hook: inject relevant memory context.

    Reads user message from stdin, searches for relevant memories,
    and outputs JSON with hookSpecificOutput.additionalContext.
    """
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            return
        event = json.loads(raw)
    except (json.JSONDecodeError, IOError):
        return

    session_id = event.get("session_id", "") or event.get("sessionId", "")
    if not session_id:
        session_id = str(uuid.uuid4())[:8]
    cwd = event.get("cwd", "") or os.getcwd()
    project = _resolve_project(cwd)

    # Extract user message
    user_message = ""
    if isinstance(event.get("tool_input"), dict):
        user_message = event["tool_input"].get("prompt", "") or event["tool_input"].get("message", "")
    elif isinstance(event.get("toolInput"), dict):
        user_message = event["toolInput"].get("prompt", "") or event["toolInput"].get("message", "")

    if not user_message:
        return

    # Get context injection
    ctx = get_context_for_injection(project, session_id, user_message, max_chars=800)
    if not ctx:
        return

    # Output JSON for Claude Code to inject
    output = json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": ctx,
        }
    }, ensure_ascii=False)
    print(output)


def handle_session_end(session_id=None):
    """Handle session end: enqueue interactions for worker to process (with retry support).

    All LLM-dependent work (reflection, batch extraction, skill extraction) is delegated
    to the worker via the queue, so transient LLM/network failures trigger automatic retries.

    IMPORTANT: This function runs as a SEPARATE PROCESS (spawned by Claude Code's hook system).
    It cannot access the in-memory _context_buffer from the main hook process.
    It MUST read interactions from the database instead.
    """
    if not session_id:
        session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if not session_id:
        # Try to find the most recent active session from DB
        conn = db.connect()
        row = conn.execute(
            "SELECT session_uuid, project FROM sessions WHERE status='active' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row:
            session_id = row["session_uuid"]
            _log.info(f"SessionEnd: no session ID from env, using most recent active session: {session_id}")
        else:
            _log.warning("SessionEnd: no session ID and no active sessions found")
            return

    # Read interactions from database (this process has no access to in-memory buffer)
    interactions = db.get_session_interactions(session_id, limit=500)
    if not interactions:
        _log.warning(f"SessionEnd [{session_id}]: no interactions found in database")
        # Still mark session as complete and enqueue for fusion
        conn = db.connect()
        row = conn.execute("SELECT project FROM sessions WHERE session_uuid=?", (session_id,)).fetchone()
        project = row["project"] if row else "unknown"
        db.enqueue(
            session_uuid=session_id,
            project=project,
            hook_event="session_end",
            extra={"interactions": [], "generate_summary": False},
        )
        return

    # Convert DB rows to the format expected by observer
    project = interactions[0]["project"] if interactions else "unknown"
    interaction_list = []
    for i in interactions:
        interaction_list.append({
            "tool_name": i["tool_name"] or i["hook_event"],
            "tool_input": i["tool_input"] or "",
            "tool_response": i["tool_response"] or "",
            "hook_event": i["hook_event"],
        })

    _log.info(f"SessionEnd [{session_id}]: enqueueing {len(interaction_list)} interactions for processing")

    # Enqueue session_end event with interactions for worker to process
    # Worker has retry logic for LLM/network failures
    db.enqueue(
        session_uuid=session_id,
        project=project,
        hook_event="session_end",
        extra={"interactions": interaction_list, "generate_summary": True, "extract_reasoning": True},
    )


def get_context_for_injection(project, session_id=None, user_message="", max_chars=800):
    """Search memories directly and inject relevant results if found."""
    if not user_message:
        return ""

    # Search memories directly (across all projects)
    results = retriever.search(user_message, project=None, limit=3)
    if not results:
        return ""

    lines = [f"[记忆系统] 找到 {len(results)} 条相关记忆:"]
    for r in results:
        title = r["title"] or ""
        content = (r["content"] or "")[:100]
        proj = r["project"] or ""
        lines.append(f"  - [{proj}] {title}: {content}")

    # Also check if a module matches
    module_id, module_name, score = retriever.match_module(user_message, project=None)
    if module_id and score >= 0.3:
        chains = db.list_reasoning_chains(module_id=module_id, limit=2)
        if chains:
            lines.append(f"相关推理链 ({len(chains)} 条):")
            for c in chains:
                q = (c["question"] or "")[:60]
                lines.append(f"  [{c['outcome']}] {q}")

    result = "\n".join(lines)
    return result[:max_chars] if len(result) > max_chars else result


def _inject_module_index(module_id, module_name, max_chars):
    """Inject matched module's index: memory titles, knowledge keys, reasoning chain count."""
    lines = []
    module = db.get_module(module_id)
    if not module:
        return ""

    mem_count = module["memory_count"] if module else 0
    project = module["project"]
    lines.append(f"[记忆系统] 匹配模块: {module_name} ({mem_count} 条记忆) [项目: {project}]")

    # Knowledge keys for this project
    knowledge = db.get_knowledge(project)
    if knowledge:
        keys = [k["key"] for k in knowledge[:8]]
        lines.append(f"知识索引: {', '.join(keys)}")

    # Reasoning chains for this module
    chains = db.list_reasoning_chains(module_id=module_id, limit=3)
    if chains:
        lines.append(f"相关推理链: {len(chains)} 条")
        for c in chains:
            q = (c["question"] or "")[:60]
            outcome = c["outcome"]
            lines.append(f"  [{outcome}] {q}")

    lines.append("(详情可通过 subagent 搜索记忆)")
    result = "\n".join(lines)
    return result[:max_chars] if len(result) > max_chars else result


def _extract_query(text):
    """Extract meaningful query terms from user message for memory search."""
    # Remove common filler words and keep substantive terms
    stopwords = {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
        "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
        "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那",
        "能", "对", "着", "把", "被", "让", "给", "从", "向", "请",
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "can", "shall", "i", "you",
        "he", "she", "it", "we", "they", "me", "him", "her", "us",
        "this", "that", "these", "those", "my", "your", "his", "its",
        "our", "their", "what", "which", "who", "how", "when", "where",
        "please", "can", "could", "would", "should", "about",
    }
    words = re.findall(r'[\w一-鿿]+', text.lower())
    meaningful = [w for w in words if len(w) > 1 and w not in stopwords]
    return " ".join(meaningful[:10])


def _resolve_project(cwd):
    """Convert working directory to project identifier."""
    if not cwd:
        return "unknown"
    parts = cwd.replace("\\", "/").rstrip("/").split("/")
    meaningful = [p for p in parts if p and not p.endswith(":") and p not in ("Users", "home")]
    if len(meaningful) >= 2:
        return "_".join(meaningful[-2:])
    return meaningful[-1] if meaningful else "unknown"


def _summarize_input(tool_name, tool_input):
    """Create a brief summary of tool input for context."""
    if not tool_input:
        return ""
    if isinstance(tool_input, dict):
        if tool_name in ("Read", "Edit", "Write"):
            return tool_input.get("file_path", "")
        if tool_name == "Bash":
            return str(tool_input.get("command", ""))[:100]
        if tool_name == "Grep":
            return tool_input.get("pattern", "")
        if tool_name == "Glob":
            return tool_input.get("pattern", "")
    return str(tool_input)[:100]


"""后台工作线程：队列消费、观察提取、事件驱动融合/剪枝、失败重试

Scheduling strategy (event-driven, not timer-based):
- Queue processing: continuous (consumes tool interactions)
- Retry: checks for retryable items every 2 minutes
- Fusion: triggered on session_end (via hook.py), NOT per-minute polling
- Pruning: runs at late night (2-5 AM) or during extended idle periods
- Reflection: triggered on session_end (via hook.py)

Offline resilience:
- LLM failures mark items as 'retry' (not 'failed')
- On startup, immediately processes pending retry items
- Periodic retry loop picks up items when LLM comes back online
"""
import time, threading, json, datetime
from lib import db, observer, fusion, pruner, config, logger

_log = logger.get()

_running = False
_worker_thread = None
_pruning_thread = None
_retry_thread = None
_last_activity = time.time()


def start():
    """Start background worker threads."""
    global _running, _worker_thread, _pruning_thread, _retry_thread
    if _running:
        _log.warning("工作线程已在运行")
        return
    _running = True

    db.connect()

    _worker_thread = threading.Thread(target=_queue_loop, daemon=True, name="memory-worker")
    _worker_thread.start()
    _log.info("队列工作线程已启动")

    _retry_thread = threading.Thread(target=_retry_loop, daemon=True, name="memory-retry")
    _retry_thread.start()
    _log.info("重试调度线程已启动")

    _pruning_thread = threading.Thread(target=_pruning_loop, daemon=True, name="memory-pruning")
    _pruning_thread.start()
    _log.info("剪枝调度线程已启动（凌晨执行）")


def stop():
    """Stop all worker threads."""
    global _running
    _running = False
    _log.info("工作线程停止中...")


def is_running():
    return _running


def get_idle_seconds():
    """Seconds since last activity."""
    return time.time() - _last_activity


def trigger_fusion(project=None):
    """Manually trigger a fusion cycle (e.g., on session end)."""
    try:
        stats = fusion.run_fusion_cycle(project=project)
        _log.info(f"Fusion triggered: {stats}")
        return stats
    except Exception as e:
        _log.error(f"Triggered fusion error: {e}")
        return None


def trigger_pruning(project=None):
    """Manually trigger a pruning cycle (e.g., on session end or shutdown)."""
    try:
        stats = pruner.run_pruning_cycle(project=project)
        _log.info(f"Pruning triggered: {stats}")
        return stats
    except Exception as e:
        _log.error(f"Triggered pruning error: {e}")
        return None


def _queue_loop():
    """Main loop: consume pending queue items."""
    global _last_activity
    while _running:
        try:
            pending = db.dequeue(limit=config.get("hook", "batch_size", default=5))
            if not pending:
                time.sleep(3)
                continue
            _last_activity = time.time()
            for item in pending:
                if not _running:
                    break
                _process_item_with_retry(item)
        except Exception as e:
            _log.error(f"队列循环错误: {e}")
            time.sleep(5)


def _retry_loop():
    """Periodically check for retryable items (LLM was unavailable earlier).

    Checks every 2 minutes. On startup, processes immediately.
    """
    # Process retryable items immediately on startup
    _process_retryable()

    RETRY_INTERVAL = 2 * 60  # 2 minutes
    while _running:
        for _ in range(RETRY_INTERVAL):
            if not _running:
                break
            time.sleep(1)
        if _running:
            _process_retryable()


def _process_retryable():
    """Process items that were marked for retry (LLM/network was unavailable)."""
    retryable = db.dequeue_retryable(limit=10)
    if not retryable:
        return
    _log.info(f"发现 {len(retryable)} 条待重试项，开始处理...")
    success = 0
    for item in retryable:
        if not _running:
            break
        try:
            _process_queue_item(item)
            db.mark_processed(item["id"])
            success += 1
        except Exception as e:
            # Still failing — mark for another retry (or permanently fail)
            _log.warning(f"重试失败 #{item['id']}: {e}")
            db.mark_failed(item["id"], str(e))
    if success:
        _log.info(f"重试完成: {success}/{len(retryable)} 条成功处理")


def _process_item_with_retry(item):
    """Process a queue item, marking for retry on LLM/network failures."""
    try:
        _process_queue_item(item)
        db.mark_processed(item["id"])
    except Exception as e:
        error_str = str(e)
        # Classify error: transient (retry) vs permanent (fail)
        is_transient = _is_transient_error(e)
        if is_transient:
            _log.warning(f"队列项 {item['id']} 暂时失败（将重试）: {error_str[:100]}")
            db.mark_failed(item["id"], error_str)  # mark_failed handles retry logic
        else:
            _log.error(f"队列项 {item['id']} 永久失败: {error_str[:100]}")
            db.mark_failed(item["id"], error_str, max_retries=0)  # no retry


def _is_transient_error(error):
    """Determine if an error is transient (network/LLM) or permanent (data/logic)."""
    error_str = str(error).lower()
    transient_keywords = [
        "timeout", "timed out", "connection", "network", "unavailable",
        "rate limit", "429", "500", "502", "503", "504",
        "ssl", "dns", "refused", "reset", "eof", "broken pipe",
        "api", "fetch", "http", "socket",
    ]
    return any(kw in error_str for kw in transient_keywords)


def _process_queue_item(item):
    """Process a single queue item. Raises on LLM/network failures."""
    event = item["hook_event"]
    project = item["project"]
    session = item["session_uuid"]

    if event == "post_tool_use":
        tool_name = item["tool_name"]
        tool_input = _safe_json(item["tool_input"])
        tool_response = _safe_json(item["tool_response"])
        context = _get_recent_context(project)
        observer.process_interaction(
            project=project,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_response=tool_response,
            context=context,
            session_uuid=session,
        )
    elif event == "session_end":
        extra = _safe_json(item["extra"], default={}) or {}
        interactions = extra.get("interactions", [])
        if interactions:
            # Batch extract observations (raises on LLM failure → triggers retry)
            saved = observer.process_batch(project, interactions, session_uuid=session)
            _log.info(f"SessionEnd [{session}]: saved {saved} observations")
            # Generate reflection (raises on LLM failure → triggers retry)
            observer.process_reflection(project, session, interactions)
            # Extract solution-oriented memories (traces back from solutions to problems)
            try:
                solution_saved = observer.process_solution_extraction(project, session, interactions)
                if solution_saved:
                    _log.info(f"SessionEnd [{session}]: extracted {solution_saved} solution memories")
            except Exception as e:
                _log.warning(f"Solution extraction failed (non-blocking): {e}")
            # Extract reusable Skill from session
            try:
                skill_id = observer.process_skill_extraction(project, session, interactions)
                if skill_id:
                    _log.info(f"SessionEnd [{session}]: extracted skill id={skill_id}")
            except Exception as e:
                _log.warning(f"Skill extraction failed (non-blocking): {e}")
            # Extract reasoning chains from significant interactions
            if extra.get("extract_reasoning"):
                try:
                    _extract_reasoning_from_session(project, session, interactions)
                except Exception as e:
                    _log.warning(f"Reasoning extraction failed (non-blocking): {e}")
            # Generate summary if requested
            if extra.get("generate_summary"):
                observer.summarize_session(project, session, interactions)
        # Always mark session as completed
        db.complete_session(session)
        _log.info(f"SessionEnd [{session}]: session marked as completed")
        # Fusion on session end (best-effort, doesn't block retry)
        try:
            fusion.run_fusion_cycle(project=project)
        except Exception as e:
            _log.warning(f"会话结束融合错误: {e}")
    elif event == "session_start":
        _log.debug(f"会话开始已处理: {session}")
    else:
        _log.debug(f"未处理的队列事件: {event}")


def _get_recent_context(project):
    """Get recent memory context for observer."""
    conn = db.connect()
    rows = conn.execute("""
        SELECT title, narrative FROM memories
        WHERE project=? AND is_active=1
        ORDER BY created_at DESC LIMIT 5
    """, (project,)).fetchall()
    return "; ".join(f"{r['title']}: {r['narrative'] or ''}" for r in rows if r["title"])


def _pruning_loop():
    """Pruning scheduler: runs at late night (2-5 AM) or during extended idle."""
    PRUNING_CHECK_INTERVAL = 30 * 60
    time.sleep(60)

    while _running:
        try:
            now = datetime.datetime.now()
            hour = now.hour
            idle = get_idle_seconds()

            should_prune = False
            reason = ""

            if 2 <= hour < 5:
                should_prune = True
                reason = f"凌晨 {hour} 点定时剪枝"
            elif idle > 7200:
                should_prune = True
                reason = f"用户空闲 {idle/3600:.1f} 小时，执行剪枝"

            if should_prune:
                _log.info(f"剪枝触发: {reason}")
                pruner.run_pruning_cycle()
        except Exception as e:
            _log.error(f"剪枝周期错误: {e}")

        for _ in range(PRUNING_CHECK_INTERVAL):
            if not _running:
                break
            time.sleep(1)


def _safe_json(text, default=None):
    if not text:
        return default if default is not None else ""
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _extract_reasoning_from_session(project, session_uuid, interactions):
    """Extract reasoning chains from session interactions.

    Identifies significant user-AI exchanges and extracts structured reasoning chains.
    Focuses on exchanges where the user asked a question and the AI provided a substantive response.
    """
    # Find user messages paired with AI responses (via assistant tool calls)
    user_messages = []
    for i, inter in enumerate(interactions):
        tool_name = inter.get("tool_name", "")
        tool_input = _safe_json(inter.get("tool_input"), default={})
        tool_response = _safe_json(inter.get("tool_response"), default={})

        # Look for Write/Edit tool calls as AI responses to user questions
        if tool_name in ("Write", "Edit") and i > 0:
            # Find the preceding user message
            for j in range(i - 1, max(i - 5, -1), -1):
                prev = interactions[j]
                if prev.get("tool_name") == "UserPromptSubmit":
                    user_msg = _safe_json(prev.get("tool_input"), default={})
                    if isinstance(user_msg, dict):
                        user_msg = user_msg.get("prompt", "")
                    if user_msg and len(str(user_msg)) > 20:
                        # Extract reasoning from this exchange
                        ai_response = str(tool_response)[:2000] if tool_response else ""
                        user_messages.append((str(user_msg)[:1000], ai_response))
                    break

    # Also capture Bash command executions as potential reasoning
    for i, inter in enumerate(interactions):
        tool_name = inter.get("tool_name", "")
        if tool_name == "Bash" and i > 0:
            tool_input = _safe_json(inter.get("tool_input"), default={})
            tool_response = _safe_json(inter.get("tool_response"), default={})
            command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
            if len(command) > 20:
                # Find context
                context_parts = []
                for j in range(max(0, i - 3), i):
                    prev = interactions[j]
                    prev_input = _safe_json(prev.get("tool_input"), default={})
                    if isinstance(prev_input, dict) and prev_input.get("prompt"):
                        context_parts.append(prev_input["prompt"][:200])
                user_msg = " ".join(context_parts) if context_parts else command[:200]
                ai_response = str(tool_response)[:1500] if tool_response else ""
                user_messages.append((user_msg, ai_response))

    if not user_messages:
        return

    # Extract reasoning chains (limit to 3 most significant)
    extracted = 0
    for user_msg, ai_response in user_messages[:3]:
        try:
            result = observer.extract_reasoning_chain(
                project=project,
                user_message=user_msg,
                ai_response=ai_response,
                session_uuid=session_uuid,
            )
            if result:
                extracted += 1
        except Exception as e:
            _log.debug(f"Reasoning extraction for exchange failed: {e}")

    if extracted > 0:
        _log.info(f"SessionEnd [{session_uuid}]: extracted {extracted} reasoning chains")

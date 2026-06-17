"""Web server: REST API + frontend for memory visualization and management."""
import json, os, threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from lib import db, config, retriever, fusion, worker, llm, logger, claude_sessions

_log = logger.get()

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")

# Translation table: replace lone surrogates (U+D800–U+DFFF) with U+FFFD
_SURROGATE_TABLE = {i: "\ufffd" for i in range(0xD800, 0xE000)}


def _sanitize(obj):
    """Recursively replace lone-surrogate characters so json.dumps + utf-8
    encoding never raises."""
    if isinstance(obj, str):
        return obj.translate(_SURROGATE_TABLE)
    if isinstance(obj, dict):
        return {_sanitize(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


class MemoryHandler(SimpleHTTPRequestHandler):
    """HTTP request handler for the memory system API and frontend."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        routes = {
            "": self._serve_index,
            "/api/stats": self._api_stats,
            "/api/stats/detailed": self._api_stats_detailed,
            "/api/memories": self._api_list_memories,
            "/api/memories/search": self._api_search,
            "/api/sessions": self._api_sessions,
            "/api/sessions/detail": self._api_session_detail,
            "/api/sessions/interactions": self._api_session_interactions,
            "/api/fusion/log": self._api_fusion_log,
            "/api/knowledge": self._api_knowledge,
            "/api/queue": self._api_queue_stats,
            "/api/health": self._api_health,
            "/api/config": self._api_get_config,
            "/api/skills": self._api_list_skills,
            "/api/skills/search": self._api_search_skills,
            "/api/skills/detail": self._api_skill_detail,
            "/api/modules": self._api_list_modules,
            "/api/modules/detail": self._api_module_detail,
            "/api/modules/memories": self._api_module_memories,
            "/api/reasoning-chains": self._api_list_reasoning_chains,
            "/api/reasoning-chains/search": self._api_search_reasoning_chains,
            "/api/reasoning-chains/detail": self._api_reasoning_chain_detail,
            "/api/reasoning-chains/stats": self._api_reasoning_chain_stats,
            "/api/claude-projects": self._api_claude_projects,
            "/api/claude-sessions": self._api_claude_sessions,
            "/api/claude-sessions/detail": self._api_claude_session_detail,
            "/api/claude-sessions/summary": self._api_claude_sessions_summary,
            "/api/knowledge/all": self._api_knowledge_all,
            "/api/modules/all": self._api_modules_all,
        }

        handler = routes.get(path)
        if handler:
            try:
                handler(params)
            except Exception as e:
                _log.error(f"API error {path}: {e}")
                self._json_response({"error": str(e)}, 500)
        elif path.startswith("/static/"):
            self._serve_static(path)
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_body()

        routes = {
            "/api/memories": self._api_create_memory,
            "/api/memories/search": self._api_search,
            "/api/memories/delete": self._api_delete_memory,
            "/api/memories/update": self._api_update_memory,
            "/api/memories/progressive-inject": self._api_progressive_inject,
            "/api/memories/reflect": self._api_reflect,
            "/api/fusion/run": self._api_run_fusion,
            "/api/config": self._api_save_config,
            "/api/llm/test": self._api_llm_test,
            "/api/worker/start": self._api_worker_start,
            "/api/worker/stop": self._api_worker_stop,
            "/api/sessions/extract": self._api_session_extract,
            "/api/memories/cleanup": self._api_cleanup_memories,
            "/api/skills": self._api_create_skill,
            "/api/skills/use": self._api_use_skill,
            "/api/skills/deactivate": self._api_deactivate_skill,
            "/api/skills/extract": self._api_extract_skill,
            "/api/modules/update-embeddings": self._api_update_module_embeddings,
            "/api/reasoning-chains": self._api_create_reasoning_chain,
            "/api/reasoning-chains/extract": self._api_extract_reasoning,
            "/api/claude-sessions/extract": self._api_claude_session_extract,
            "/api/claude-sessions/batch-extract": self._api_claude_batch_extract,
            "/api/reasoning-chains/cleanup": self._api_cleanup_reasoning_chains,
            "/api/memories/cleanup-generic": self._api_cleanup_generic_memories,
        }

        handler = routes.get(path)
        if handler:
            try:
                handler(body)
            except Exception as e:
                _log.error(f"API error {path}: {e}")
                self._json_response({"error": str(e)}, 500)
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        body = self._read_body()
        if path == "/api/memories":
            self._api_delete_memory(body)
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        origin = self.headers.get("Origin", "")
        if _is_local_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── Frontend ──────────────────────────────────────────────────

    def _serve_index(self, params=None):
        html_path = os.path.join(TEMPLATE_DIR, "index.html")
        with open(html_path, encoding="utf-8") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "private, max-age=10, must-revalidate")
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def _serve_static(self, path):
        # Prevent path traversal: normalize and restrict to static/ directory
        base_dir = os.path.realpath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "static"))
        # Strip leading /static/ and normalize
        rel_path = path.lstrip("/")
        if rel_path.startswith("static/"):
            rel_path = rel_path[len("static/"):]
        file_path = os.path.realpath(os.path.join(base_dir, rel_path))
        # Ensure the resolved path is within the static directory
        if not file_path.startswith(base_dir + os.sep) and file_path != base_dir:
            self._json_response({"error": "Forbidden"}, 403)
            return
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            self._json_response({"error": "Not found"}, 404)
            return
        ext = os.path.splitext(file_path)[1]
        content_types = {".css": "text/css", ".js": "application/javascript", ".png": "image/png",
                         ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".ico": "image/x-icon"}
        with open(file_path, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_types.get(ext, "application/octet-stream"))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        self.wfile.write(content)

    # ── API Endpoints ─────────────────────────────────────────────

    def _api_stats(self, params=None):
        self._json_response(db.stats(), cache_seconds=5)

    def _api_list_memories(self, params=None):
        project = _first(params, "project")
        category = _first(params, "category")
        limit = int(_first(params, "limit", "50"))
        offset = int(_first(params, "offset", "0"))
        memories = db.list_memories(project=project, category=category, limit=limit, offset=offset)
        total = db.count_memories(project=project)
        self._json_response({
            "memories": [_row_to_dict(m) for m in memories],
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    def _api_search(self, params_or_body=None):
        p = params_or_body or {}
        # Support both GET (parse_qs lists) and POST (JSON scalars)
        query = _first(p, "query") or _first(p, "q") or ""
        project = _first(p, "project")
        category = _first(p, "category")
        limit = int(_first(p, "limit", "10"))
        if not query:
            self._json_response({"error": "query required"}, 400)
            return
        results = retriever.search(query, project=project, limit=limit, category=category)
        self._json_response({"results": [_row_to_dict(m) for m in results], "query": query})

    def _api_create_memory(self, body):
        required = ["project", "title"]
        for field in required:
            if not body.get(field):
                self._json_response({"error": f"{field} required"}, 400)
                return
        mem_id = db.insert_memory(
            project=body["project"],
            category=body.get("category", "observation"),
            obs_type=body.get("obs_type"),
            memory_type=body.get("memory_type", "episodic"),
            title=body["title"],
            subtitle=body.get("subtitle"),
            narrative=body.get("narrative"),
            facts=body.get("facts", []),
            concepts=body.get("concepts", []),
            name=body.get("name"),
            description=body.get("description"),
            content=body.get("content") or body.get("narrative"),
            metadata=body.get("metadata"),
            importance=body.get("importance"),
        )
        # Update embedding in background thread (don't block response)
        text = body.get("title", "") + " " + (body.get("narrative") or "") + " " + (body.get("content") or "")
        import threading
        threading.Thread(
            target=lambda: _safe_update_embedding(mem_id, text),
            daemon=True, name="embedding-update"
        ).start()
        self._json_response({"id": mem_id, "status": "created"})

    def _api_delete_memory(self, body):
        mem_id = body.get("id")
        if not mem_id:
            self._json_response({"error": "id required"}, 400)
            return
        db.deactivate_memory(mem_id, reason="manual delete via UI")
        self._json_response({"status": "deleted", "id": mem_id})

    def _api_update_memory(self, body):
        mem_id = body.get("id")
        if not mem_id:
            self._json_response({"error": "id required"}, 400)
            return
        # Whitelist allowed fields to prevent injection
        ALLOWED = {"category", "obs_type", "memory_type", "title", "subtitle",
                    "narrative", "facts", "concepts", "name", "description",
                    "content", "importance", "metadata", "module_id", "related_to"}
        fields = {k: v for k, v in body.items() if k != "id" and v is not None and k in ALLOWED}
        if "facts" in fields and isinstance(fields["facts"], list):
            fields["facts"] = json.dumps(fields["facts"], ensure_ascii=False)
        if "concepts" in fields and isinstance(fields["concepts"], list):
            fields["concepts"] = json.dumps(fields["concepts"], ensure_ascii=False)
        db.update_memory(mem_id, **fields)
        self._json_response({"status": "updated", "id": mem_id})

    def _api_sessions(self, params=None):
        project = _first(params, "project")
        limit = int(_first(params, "limit", "50"))
        sessions = db.get_sessions(project=project, limit=limit)
        self._json_response({"sessions": [_row_to_dict(s) for s in sessions]})

    def _api_session_detail(self, params=None):
        uuid = _first(params, "uuid")
        if not uuid:
            self._json_response({"error": "uuid required"}, 400)
            return
        detail = db.get_session_detail(uuid)
        if not detail:
            self._json_response({"error": "Session not found"}, 404)
            return
        self._json_response({
            "session": _row_to_dict(detail["session"]),
            "memories": [_row_to_dict(m) for m in detail["memories"]],
            "interactions": [_row_to_dict(i) for i in detail["interactions"]],
        })

    def _api_session_interactions(self, params=None):
        uuid = _first(params, "uuid")
        if not uuid:
            self._json_response({"error": "uuid required"}, 400)
            return
        limit = int(_first(params, "limit", "200"))
        rows = db.get_session_interactions(uuid, limit=limit)
        self._json_response({"interactions": [_row_to_dict(r) for r in rows]})

    def _api_fusion_log(self, params=None):
        limit = int(_first(params, "limit", "50"))
        logs = db.get_fusion_log(limit=limit)
        self._json_response({"log": [_row_to_dict(l) for l in logs]})

    def _api_knowledge(self, params=None):
        project = _first(params, "project")
        entries = db.get_knowledge(project)
        self._json_response({"knowledge": [_row_to_dict(e) for e in entries]})

    def _api_queue_stats(self, params=None):
        stats = db.queue_stats()
        self._json_response({"queue": {r["status"]: r["cnt"] for r in stats}})

    def _api_health(self, params=None):
        queue = db.queue_stats()
        queue_dict = {r["status"]: r["cnt"] for r in queue}
        self._json_response({
            "status": "ok",
            "worker_running": worker.is_running(),
            "db_connected": True,
            "pending_retry": queue_dict.get("retry", 0),
            "pending_queue": queue_dict.get("pending", 0),
        }, cache_seconds=5)

    def _api_get_config(self, params=None):
        cfg = config.load()
        # Mask API key: only show last 4 characters
        safe_cfg = json.loads(json.dumps(cfg))  # deep copy
        if "llm" in safe_cfg and safe_cfg["llm"].get("api_key"):
            key = safe_cfg["llm"]["api_key"]
            if len(key) > 8:
                safe_cfg["llm"]["api_key"] = "•" * (len(key) - 4) + key[-4:]
            else:
                safe_cfg["llm"]["api_key"] = "•" * len(key)
        self._json_response(safe_cfg)

    def _api_save_config(self, body):
        # Preserve existing API key if the masked placeholder is sent back
        existing = config.load()
        existing_key = existing.get("llm", {}).get("api_key", "")
        new_key = body.get("llm", {}).get("api_key", "")
        if new_key and "•" in new_key:
            # Masked value sent back — keep the real key
            if "llm" not in body:
                body["llm"] = {}
            body["llm"]["api_key"] = existing_key
        config.save(body)
        self._json_response({"status": "saved"})

    def _api_llm_test(self, body):
        result = llm.test_connection(
            base_url=body.get("base_url"),
            api_key=body.get("api_key"),
            model=body.get("model"),
        )
        self._json_response(result)

    def _api_session_extract(self, body):
        uuid = body.get("uuid")
        if not uuid:
            self._json_response({"error": "uuid required"}, 400)
            return
        interactions = db.get_session_interactions(uuid, limit=200)
        if not interactions:
            self._json_response({"error": "No interactions found"}, 404)
            return
        project = interactions[0]["project"] if interactions else "unknown"
        # Batch extract observations
        from lib import observer
        saved = observer.process_batch(
            project=project,
            interactions=[dict(i) for i in interactions],
            context="",
            session_uuid=uuid,
        )
        self._json_response({"status": "completed", "memories_created": saved})

    def _api_cleanup_memories(self, body=None):
        from lib import pruner
        stats = pruner.run_pruning_cycle()
        self._json_response({"status": "completed", "stats": stats})

    def _api_cleanup_reasoning_chains(self, body=None):
        """Clean up low-quality reasoning chains."""
        min_importance = (body or {}).get("min_importance", 4)
        stats = db.cleanup_low_quality_reasoning_chains(min_importance=min_importance)
        self._json_response({"status": "completed", "stats": stats})

    def _api_cleanup_generic_memories(self, body=None):
        """Clean up memories with generic/useless narratives."""
        stats = db.cleanup_generic_memories()
        self._json_response({"status": "completed", "stats": stats})

    def _api_stats_detailed(self, params=None):
        """Detailed stats including memory type breakdown and decay info."""
        stats = db.stats()
        conn = db.connect()
        # Add decay statistics
        decay_rows = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN metadata LIKE '%"retention"%' THEN 1 ELSE 0 END) as tracked,
                AVG(CASE WHEN stability IS NOT NULL THEN stability ELSE 1.0 END) as avg_stability,
                AVG(CASE WHEN access_count IS NOT NULL THEN access_count ELSE 0 END) as avg_access
            FROM memories WHERE is_active=1
        """).fetchone()
        stats["decay"] = {
            "total_tracked": decay_rows["tracked"] or 0 if decay_rows else 0,
            "avg_stability": round(decay_rows["avg_stability"] or 1.0, 2) if decay_rows else 1.0,
            "avg_access_count": round(decay_rows["avg_access"] or 0, 1) if decay_rows else 0,
        }
        self._json_response(stats)

    def _api_progressive_inject(self, body):
        """Progressive memory injection based on session phase."""
        project = body.get("project")
        session_id = body.get("session_id")
        user_message = body.get("message", "")
        max_chars = int(body.get("max_chars", 800))
        if not project:
            self._json_response({"error": "project required"}, 400)
            return
        from lib import hook
        context = hook.get_context_for_injection(project, session_id, user_message, max_chars)
        self._json_response({"context": context, "session_id": session_id})

    def _api_reflect(self, body):
        """Manually trigger reflection generation for a session."""
        uuid = body.get("uuid")
        if not uuid:
            self._json_response({"error": "uuid required"}, 400)
            return
        interactions = db.get_session_interactions(uuid, limit=200)
        if not interactions:
            self._json_response({"error": "No interactions found"}, 404)
            return
        project = interactions[0]["project"] if interactions else "unknown"
        from lib import observer
        result = observer.process_reflection(project, uuid, [dict(i) for i in interactions])
        if result:
            self._json_response({"status": "completed", "memory_id": result})
        else:
            self._json_response({"status": "no_reflection_generated"})

    def _api_run_fusion(self, body=None):
        project = (body or {}).get("project")
        stats = fusion.run_fusion_cycle(project=project)
        self._json_response({"status": "completed", "stats": stats})

    def _api_worker_start(self, body=None):
        worker.start()
        self._json_response({"status": "started"})

    def _api_worker_stop(self, body=None):
        worker.stop()
        self._json_response({"status": "stopped"})

    # ── Claude Code Sessions API ────────────────────────────────

    def _api_claude_projects(self, params=None):
        projects = claude_sessions.list_projects()
        self._json_response({"projects": projects, "total": len(projects)})

    def _api_claude_sessions(self, params=None):
        project = _first(params, "project")
        limit = int(_first(params, "limit", "100"))
        offset = int(_first(params, "offset", "0"))
        sessions, total = claude_sessions.list_sessions(project=project, limit=limit, offset=offset)
        self._json_response({"sessions": sessions, "total": total, "limit": limit, "offset": offset})

    def _api_claude_session_detail(self, params=None):
        session_id = _first(params, "id")
        project = _first(params, "project")
        if not session_id:
            self._json_response({"error": "id required"}, 400)
            return
        detail = claude_sessions.get_session_messages(session_id, project=project)
        if not detail:
            self._json_response({"error": "Session not found"}, 404)
            return
        self._json_response({"session": detail})

    def _api_claude_sessions_summary(self, params=None):
        """Lightweight summary: project counts + 5 most recent sessions.
        Avoids reading all JSONL files — only touches the 5 newest."""
        n = int(_first(params, "recent", "5"))
        summary = claude_sessions.get_recent_sessions_fast(n=min(n, 20))
        self._json_response(summary, cache_seconds=10)

    def _api_knowledge_all(self, params=None):
        """Return all knowledge entries across all projects in one call."""
        conn = db.connect()
        rows = conn.execute(
            "SELECT * FROM knowledge_index ORDER BY updated_at DESC"
        ).fetchall()
        self._json_response({"knowledge": [_row_to_dict(r) for r in rows]}, cache_seconds=10)

    def _api_modules_all(self, params=None):
        """Return all modules across all projects in one call."""
        conn = db.connect()
        rows = conn.execute(
            "SELECT * FROM memory_modules ORDER BY updated_at DESC"
        ).fetchall()
        self._json_response({"modules": [_row_to_dict(r) for r in rows]}, cache_seconds=10)

    def _api_claude_session_extract(self, body):
        """Extract memories from a Claude Code session JSONL transcript."""
        session_id = body.get("id")
        project = body.get("project", "")
        if not session_id:
            self._json_response({"error": "id required"}, 400)
            return
        detail = claude_sessions.get_session_messages(session_id, project=project)
        if not detail or not detail.get("messages"):
            self._json_response({"error": "Session not found or empty"}, 404)
            return
        # Convert messages to interaction format for observer
        interactions = []
        for msg in detail["messages"]:
            interactions.append({
                "tool_name": "conversation",
                "tool_input": "" if msg["role"] == "assistant" else msg["content"][:500],
                "tool_response": msg["content"][:1000] if msg["role"] == "assistant" else "",
                "hook_event": "message",
            })
        # Determine project name from cwd or project dir
        proj = project.replace("-", "_").replace("--", "_").strip("_")
        if detail.get("cwd"):
            from lib.hook import _resolve_project
            proj = _resolve_project(detail["cwd"])
        from lib import observer
        saved = observer.process_batch(
            project=proj,
            interactions=interactions[:100],  # Limit to avoid overload
            context=f"Claude Code session {session_id}",
            session_uuid=session_id,
        )
        self._json_response({"status": "completed", "memories_created": saved})

    def _api_claude_batch_extract(self, body):
        """Batch extract memories and reasoning chains from all Claude Code sessions."""
        limit = body.get("limit", 50)
        min_msgs = body.get("min_msgs", 5)

        all_sessions, _ = claude_sessions.list_sessions(limit=500)
        # Filter: skip sessions with too few messages
        candidates = [s for s in all_sessions if s["user_msg_count"] > min_msgs]

        # Skip sessions that already have memories
        conn = db.connect()
        existing = conn.execute(
            "SELECT DISTINCT origin_session FROM memories WHERE origin_session IS NOT NULL"
        ).fetchall()
        existing_ids = {r["origin_session"] for r in existing}
        candidates = [s for s in candidates if s["session_id"] not in existing_ids]

        import threading

        def _process_session(s):
            detail = claude_sessions.get_session_messages(s["session_id"], s["project"])
            if not detail or not detail.get("messages"):
                return 0
            messages = detail["messages"]
            interactions = []
            for msg in messages:
                interactions.append({
                    "tool_name": "conversation",
                    "tool_input": "" if msg["role"] == "assistant" else msg["content"][:500],
                    "tool_response": msg["content"][:1000] if msg["role"] == "assistant" else "",
                    "hook_event": "message",
                })
            # Resolve project name
            proj = s["project"].replace("-", "_").replace("--", "_").strip("_")
            if detail.get("cwd"):
                from lib.hook import _resolve_project
                proj = _resolve_project(detail["cwd"])
            from lib import observer
            saved = observer.process_batch(
                project=proj,
                interactions=interactions[:100],
                context=f"Claude Code session {s['session_id']}",
                session_uuid=s["session_id"],
            )
            # Extract reasoning chains from user-AI pairs
            _extract_reasoning_from_messages(proj, s["session_id"], messages)
            return saved

        def _extract_reasoning_from_messages(project, session_id, messages):
            """Extract reasoning chains from user-AI message pairs."""
            from lib import observer
            for i, msg in enumerate(messages):
                if msg["role"] != "user" or i + 1 >= len(messages):
                    continue
                next_msg = messages[i + 1]
                if next_msg["role"] != "assistant":
                    continue
                user_text = msg["content"][:1000]
                ai_text = next_msg["content"][:2000]
                if len(user_text) < 10 or len(ai_text) < 20:
                    continue
                try:
                    observer.extract_reasoning_chain(
                        project=project,
                        user_message=user_text,
                        ai_response=ai_text,
                        session_uuid=session_id,
                    )
                except Exception:
                    pass

        def _batch_worker():
            total_saved = 0
            for i, s in enumerate(candidates[:limit]):
                try:
                    saved = _process_session(s)
                    total_saved += saved
                    _log.info(f"Batch extract [{i+1}/{min(len(candidates), limit)}]: {s['session_id'][:12]} -> {saved} memories")
                except Exception as e:
                    _log.warning(f"Batch extract failed for {s['session_id'][:12]}: {e}")
            _log.info(f"Batch extract complete: {total_saved} memories from {min(len(candidates), limit)} sessions")

        threading.Thread(target=_batch_worker, daemon=True, name="batch-extract").start()
        self._json_response({
            "status": "started",
            "sessions_to_process": min(len(candidates), limit),
            "total_candidates": len(candidates),
        })

    # ── Modules API ─────────────────────────────────────────────

    def _api_list_modules(self, params=None):
        project = _first(params, "project")
        modules = db.get_modules(project)
        self._json_response({"modules": [_row_to_dict(m) for m in modules]})

    def _api_module_detail(self, params=None):
        module_id = _first(params, "id")
        if not module_id:
            self._json_response({"error": "id required"}, 400)
            return
        module = db.get_module(int(module_id))
        if not module:
            self._json_response({"error": "Module not found"}, 404)
            return
        memories = db.get_memories_by_module(int(module_id), limit=50)
        self._json_response({
            "module": _row_to_dict(module),
            "memories": [_row_to_dict(m) for m in memories],
        })

    def _api_module_memories(self, params=None):
        module_id = _first(params, "id")
        limit = int(_first(params, "limit", "50"))
        if not module_id:
            self._json_response({"error": "id required"}, 400)
            return
        memories = db.get_memories_by_module(int(module_id), limit=limit)
        self._json_response({"memories": [_row_to_dict(m) for m in memories]})

    def _api_update_module_embeddings(self, body=None):
        project = (body or {}).get("project")
        if not project:
            self._json_response({"error": "project required"}, 400)
            return
        retriever.update_all_module_embeddings(project)
        modules = db.get_modules(project)
        self._json_response({"status": "completed", "modules_updated": len(modules)})

    # ── Reasoning Chains API ─────────────────────────────────────

    def _api_list_reasoning_chains(self, params=None):
        project = _first(params, "project")
        mode = _first(params, "mode")
        outcome = _first(params, "outcome")
        limit = int(_first(params, "limit", "50"))
        offset = int(_first(params, "offset", "0"))
        chains = db.list_reasoning_chains(
            project=project, thinking_mode=mode, outcome=outcome,
            limit=limit, offset=offset,
        )
        total = db.count_reasoning_chains(project=project)
        self._json_response({
            "chains": [_row_to_dict(c) for c in chains],
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    def _api_search_reasoning_chains(self, params=None):
        query = _first(params, "q") or _first(params, "query")
        project = _first(params, "project")
        limit = int(_first(params, "limit", "10"))
        if not query:
            self._json_response({"error": "q (query) required"}, 400)
            return
        results = retriever.search_reasoning_chains(query, project=project, limit=limit)
        self._json_response({"chains": [_row_to_dict(c) for c in results], "query": query})

    def _api_reasoning_chain_detail(self, params=None):
        chain_id = _first(params, "id")
        if not chain_id:
            self._json_response({"error": "id required"}, 400)
            return
        chain = db.get_reasoning_chain(int(chain_id))
        if not chain:
            self._json_response({"error": "Chain not found"}, 404)
            return
        self._json_response({"chain": _row_to_dict(chain)})

    def _api_reasoning_chain_stats(self, params=None):
        project = _first(params, "project")
        total = db.count_reasoning_chains(project=project)
        conn = db.connect()
        sql = "SELECT thinking_mode, outcome, COUNT(*) as cnt FROM reasoning_chains WHERE is_active=1"
        params_list = []
        if project:
            sql += " AND project=?"
            params_list.append(project)
        sql += " GROUP BY thinking_mode, outcome"
        rows = conn.execute(sql, params_list).fetchall()
        breakdown = {}
        for r in rows:
            mode = r["thinking_mode"]
            outcome = r["outcome"]
            if mode not in breakdown:
                breakdown[mode] = {}
            breakdown[mode][outcome] = r["cnt"]
        self._json_response({"total": total, "breakdown": breakdown})

    def _api_create_reasoning_chain(self, body):
        project = body.get("project")
        if not project:
            self._json_response({"error": "project required"}, 400)
            return
        chain_id = db.insert_reasoning_chain(
            project=project,
            question=body.get("question"),
            steps=body.get("steps", []),
            outcome=body.get("outcome", "pending"),
            outcome_summary=body.get("outcome_summary"),
            failure_reason=body.get("failure_reason"),
            extracted_facts=body.get("extracted_facts", []),
            session_uuid=body.get("session_uuid"),
            importance=body.get("importance", 5),
        )
        # Update embedding in background
        text = (body.get("question") or "") + " " + (body.get("outcome_summary") or "")
        import threading
        threading.Thread(
            target=lambda: _safe_update_reasoning_embedding(chain_id, text),
            daemon=True, name="reasoning-embedding-update"
        ).start()
        self._json_response({"id": chain_id, "status": "created"})

    def _api_extract_reasoning(self, body):
        """Extract reasoning chain from a user message and AI response."""
        project = body.get("project")
        user_message = body.get("user_message", "")
        ai_response = body.get("ai_response", "")
        if not project or not user_message:
            self._json_response({"error": "project and user_message required"}, 400)
            return
        from lib import observer
        result = observer.extract_reasoning_chain(
            project=project,
            user_message=user_message,
            ai_response=ai_response,
            session_uuid=body.get("session_uuid"),
        )
        if result:
            self._json_response({"status": "extracted", "chain": result})
        else:
            self._json_response({"status": "no_chain_extracted"})

    # ── Skills API ─────────────────────────────────────────────

    def _api_list_skills(self, params=None):
        project = _first(params, "project")
        limit = int(_first(params, "limit", "50"))
        skills = db.get_skills(project=project, limit=limit)
        self._json_response({"skills": [_row_to_dict(s) for s in skills]})

    def _api_search_skills(self, params=None):
        query = _first(params, "q") or _first(params, "query")
        project = _first(params, "project")
        if not query:
            self._json_response({"error": "q (query) required"}, 400)
            return
        results = db.search_skills(query, project=project)
        self._json_response({"skills": [_row_to_dict(s) for s in results], "query": query})

    def _api_skill_detail(self, params=None):
        skill_id = _first(params, "id")
        if not skill_id:
            self._json_response({"error": "id required"}, 400)
            return
        skill = db.get_skill(int(skill_id))
        if not skill:
            self._json_response({"error": "Skill not found"}, 404)
            return
        self._json_response({"skill": _row_to_dict(skill)})

    def _api_create_skill(self, body):
        required = ["project", "name"]
        for field in required:
            if not body.get(field):
                self._json_response({"error": f"{field} required"}, 400)
                return
        skill_id = db.insert_skill(
            project=body["project"],
            name=body["name"],
            description=body.get("description", ""),
            workflow=body.get("workflow", []),
            trigger_keywords=body.get("trigger_keywords", []),
            stop_conditions=body.get("stop_conditions", []),
            output_format=body.get("output_format", ""),
            examples=body.get("examples", []),
            gotchas=body.get("gotchas", []),
            references_list=body.get("references", []),
            confidence=body.get("confidence", 3),
        )
        self._json_response({"id": skill_id, "status": "created"})

    def _api_use_skill(self, body):
        skill_id = body.get("id")
        if not skill_id:
            self._json_response({"error": "id required"}, 400)
            return
        db.use_skill(int(skill_id))
        self._json_response({"status": "used", "id": skill_id})

    def _api_deactivate_skill(self, body):
        skill_id = body.get("id")
        if not skill_id:
            self._json_response({"error": "id required"}, 400)
            return
        db.deactivate_skill(int(skill_id))
        self._json_response({"status": "deactivated", "id": skill_id})

    def _api_extract_skill(self, body):
        uuid = body.get("uuid")
        if not uuid:
            self._json_response({"error": "uuid required"}, 400)
            return
        interactions = db.get_session_interactions(uuid, limit=200)
        if not interactions:
            self._json_response({"error": "No interactions found"}, 404)
            return
        project = interactions[0]["project"] if interactions else "unknown"
        from lib import observer
        skill_id = observer.process_skill_extraction(
            project=project,
            session_uuid=uuid,
            interactions=[dict(i) for i in interactions],
        )
        if skill_id:
            self._json_response({"status": "extracted", "skill_id": skill_id})
        else:
            self._json_response({"status": "no_skill_extracted"})

    # ── Helpers ───────────────────────────────────────────────────

    def _json_response(self, data, code=200, cache_seconds=0):
        body = json.dumps(_sanitize(data), ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = self.headers.get("Origin", "")
        if _is_local_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
        if cache_seconds > 0:
            self.send_header("Cache-Control", f"private, max-age={cache_seconds}")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def log_message(self, format, *args):
        _log.debug(f"HTTP {args[0]}")


def _safe_update_embedding(mem_id, text):
    """Update embedding in background, catching all exceptions."""
    try:
        retriever.update_embedding(mem_id, text)
    except Exception as e:
        _log.warning(f"Background embedding update failed for #{mem_id}: {e}")


def _safe_update_reasoning_embedding(chain_id, text):
    """Update reasoning chain embedding in background."""
    try:
        emb_bytes = retriever.compute_embedding(text)
        if emb_bytes:
            conn = db.connect()
            conn.execute("UPDATE reasoning_chains SET embedding=? WHERE id=?", (emb_bytes, chain_id))
            conn.commit()
    except Exception as e:
        _log.warning(f"Background reasoning embedding update failed for #{chain_id}: {e}")


def _first(params, key, default=None):
    if params is None:
        return default
    val = params.get(key)
    if val is None:
        return default
    return val[0] if isinstance(val, list) else val


def _is_local_origin(origin):
    """Check if the origin is a local address (localhost or 127.0.0.1)."""
    if not origin:
        return False
    return any(origin.startswith(p) for p in (
        "http://127.0.0.1:", "http://localhost:",
        "https://127.0.0.1:", "https://localhost:",
    ))


def _row_to_dict(row):
    if row is None:
        return None
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(row)


def start_server(host=None, port=None):
    """Start the HTTP server in a background thread."""
    cfg = config.get("server") or {}
    host = host or cfg.get("host", "127.0.0.1")
    port = port or cfg.get("port", 38800)
    server = HTTPServer((host, port), MemoryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="memory-server")
    thread.start()
    _log.info(f"Memory server running at http://{host}:{port}")
    return server

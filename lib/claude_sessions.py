"""Read Claude Code session transcripts directly from ~/.claude/projects/."""
import json, os, glob
from lib import logger

_log = logger.get()

CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")


def list_projects():
    """List all Claude Code project directories."""
    if not os.path.isdir(CLAUDE_PROJECTS_DIR):
        return []
    projects = []
    for name in os.listdir(CLAUDE_PROJECTS_DIR):
        p = os.path.join(CLAUDE_PROJECTS_DIR, name)
        if os.path.isdir(p):
            jsonl_count = len(glob.glob(os.path.join(p, "*.jsonl")))
            if jsonl_count > 0:
                projects.append({"id": name, "session_count": jsonl_count})
    projects.sort(key=lambda x: x["session_count"], reverse=True)
    return projects


def get_recent_sessions_fast(n=5):
    """Fast path: return project summary + N most recent session metadata.

    Uses file mtime to avoid reading every JSONL — only the N newest files
    (by modification time) are opened for metadata extraction.
    """
    if not os.path.isdir(CLAUDE_PROJECTS_DIR):
        return {"projects": [], "total_sessions": 0, "recent_sessions": []}

    # 1) Project summary — only counts files via glob (no file reading)
    project_summary = []
    all_files = []  # (mtime, filepath, project_dir)

    for name in os.listdir(CLAUDE_PROJECTS_DIR):
        proj_path = os.path.join(CLAUDE_PROJECTS_DIR, name)
        if not os.path.isdir(proj_path):
            continue
        files = glob.glob(os.path.join(proj_path, "*.jsonl"))
        if not files:
            continue
        project_summary.append({"id": name, "session_count": len(files)})
        for fp in files:
            try:
                all_files.append((os.path.getmtime(fp), fp, name))
            except OSError:
                pass

    project_summary.sort(key=lambda x: x["session_count"], reverse=True)
    total_sessions = sum(p["session_count"] for p in project_summary)

    # 2) Only read metadata for the N most recently modified files
    all_files.sort(key=lambda x: x[0], reverse=True)
    recent = []
    for _mtime, fp, proj_dir in all_files[:n]:
        meta = _extract_metadata(fp, proj_dir)
        if meta:
            recent.append(meta)

    return {
        "projects": project_summary,
        "total_sessions": total_sessions,
        "recent_sessions": recent,
    }


def list_sessions(project=None, limit=100, offset=0):
    """List sessions from Claude Code transcripts. Returns metadata only."""
    sessions = []
    if project:
        proj_dirs = [project]
    else:
        proj_dirs = [d for d in os.listdir(CLAUDE_PROJECTS_DIR)
                     if os.path.isdir(os.path.join(CLAUDE_PROJECTS_DIR, d))]

    for proj_dir in proj_dirs:
        proj_path = os.path.join(CLAUDE_PROJECTS_DIR, proj_dir)
        if not os.path.isdir(proj_path):
            continue
        for f in glob.glob(os.path.join(proj_path, "*.jsonl")):
            meta = _extract_metadata(f, proj_dir)
            if meta:
                sessions.append(meta)

    sessions.sort(key=lambda x: x.get("last_ts") or "", reverse=True)
    total = len(sessions)
    return sessions[offset:offset + limit], total


def get_session_messages(session_id, project=None):
    """Load full message history for a session."""
    fpath = _find_session_file(session_id, project)
    if not fpath:
        return None

    messages = []
    meta = None
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue

                if d.get("type") not in ("user", "assistant"):
                    continue

                msg = d.get("message", {})
                if not isinstance(msg, dict):
                    continue

                role = msg.get("role", d.get("type", "unknown"))
                content = msg.get("content", "")

                text = _extract_text(content)
                if not text:
                    continue

                if text.startswith("<") and ">" in text[:50]:
                    continue

                messages.append({
                    "role": role,
                    "content": text[:5000],
                    "timestamp": d.get("timestamp"),
                })

                if meta is None:
                    meta = {
                        "session_id": session_id,
                        "project": project or "",
                        "cwd": d.get("cwd", ""),
                        "git_branch": d.get("gitBranch", ""),
                        "version": d.get("version", ""),
                        "entrypoint": d.get("entrypoint", ""),
                    }
    except Exception as e:
        _log.warning(f"Failed to read session {session_id}: {e}")
        return None

    if meta:
        meta["messages"] = messages
        meta["message_count"] = len(messages)
    return meta


def _extract_metadata(fpath, project_dir):
    """Extract session metadata from a JSONL file without reading all messages."""
    session_id = os.path.basename(fpath).replace(".jsonl", "")
    first_ts = None
    last_ts = None
    user_msg_count = 0
    assistant_msg_count = 0
    first_user_msg = ""
    cwd = ""
    git_branch = ""

    try:
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue

                ts = d.get("timestamp")
                if ts and not first_ts:
                    first_ts = ts
                if ts:
                    last_ts = ts

                if not cwd:
                    cwd = d.get("cwd", "")
                if not git_branch:
                    git_branch = d.get("gitBranch", "")

                if d.get("type") == "user":
                    user_msg_count += 1
                    if not first_user_msg:
                        msg = d.get("message", {})
                        if isinstance(msg, dict):
                            content = msg.get("content", "")
                            text = _extract_text(content)
                            if text and not text.startswith("<"):
                                first_user_msg = text[:200]
                elif d.get("type") == "assistant":
                    assistant_msg_count += 1
    except Exception as e:
        _log.debug(f"Failed to read metadata for {session_id}: {e}")
        return None

    return {
        "session_id": session_id,
        "project": project_dir,
        "cwd": cwd,
        "git_branch": git_branch,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "user_msg_count": user_msg_count,
        "assistant_msg_count": assistant_msg_count,
        "first_user_msg": first_user_msg,
    }


def _find_session_file(session_id, project=None):
    """Find the JSONL file for a session."""
    if project:
        fpath = os.path.join(CLAUDE_PROJECTS_DIR, project, f"{session_id}.jsonl")
        if os.path.isfile(fpath):
            return fpath
        return None

    for proj_dir in os.listdir(CLAUDE_PROJECTS_DIR):
        fpath = os.path.join(CLAUDE_PROJECTS_DIR, proj_dir, f"{session_id}.jsonl")
        if os.path.isfile(fpath):
            return fpath
    return None


def _extract_text(content):
    """Extract plain text from message content (string or list format)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    parts.append(c.get("text", ""))
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(parts).strip()
    return ""

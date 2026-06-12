import json, os, pathlib

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"

_config = None
_mtime = 0

def _load_env():
    """Load .env file into environment variables."""
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())

def load() -> dict:
    global _config, _mtime
    try:
        st = CONFIG_PATH.stat()
        if _config is not None and st.st_mtime == _mtime:
            return _config
        _mtime = st.st_mtime
    except OSError:
        pass
    _load_env()
    with open(CONFIG_PATH, encoding="utf-8") as f:
        _config = json.load(f)
    # Resolve relative paths against BASE_DIR
    _config["db_path"] = str(BASE_DIR / "data" / "memory.db")
    _config["log_dir"] = str(BASE_DIR / "logs")
    os.makedirs(_config["log_dir"], exist_ok=True)
    os.makedirs(os.path.dirname(_config["db_path"]), exist_ok=True)
    return _config

def save(cfg: dict):
    global _config
    _config = cfg
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def get(*keys, default=None):
    cfg = load()
    node = cfg
    for k in keys:
        if isinstance(node, dict):
            node = node.get(k)
        else:
            return default
        if node is None:
            return default
    return node

import logging, os
from lib import config

_logger = None

def get():
    global _logger
    if _logger is not None:
        return _logger
    cfg = config.load()
    _logger = logging.getLogger("memory-system")
    _logger.setLevel(getattr(logging, cfg.get("log_level", "INFO")))
    fmt = logging.Formatter("[%(asctime)s] [%(levelname)-5s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    # File handler
    import datetime
    log_file = os.path.join(cfg["log_dir"], f"memory-{datetime.date.today()}.log")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    _logger.addHandler(fh)
    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    _logger.addHandler(ch)
    return _logger

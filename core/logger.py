import logging
import json
import sys
from datetime import datetime
from typing import Any, Dict

# Keys injected via `extra={}` that we want to capture in JSON output
_EXTRA_KEYS = {
    "source_endpoint", "latency", "model", "preferred_tier",
    "mode", "target_url", "error", "task_id"
}

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        # Capture any extra fields that were passed via extra={}
        for key in _EXTRA_KEYS:
            val = getattr(record, key, None)
            if val is not None:
                log_data[key] = val
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)

def setup_logger(name: str, level: str = "INFO"):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    
    # Remove existing handlers to avoid duplicates
    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.addHandler(handler)
    return logger

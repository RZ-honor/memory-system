"""LLM client with multi-provider support, retry, and fallback."""
import json, time, urllib.request, urllib.error
from lib import config, logger

_log = logger.get()


class LLMClient:
    def __init__(self):
        self._reload()

    def _reload(self):
        import os
        cfg = config.get("llm") or {}
        self.provider = cfg.get("provider", "anthropic")
        self.base_url = (cfg.get("base_url") or os.environ.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
        self.api_key = cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or ""
        self.model = cfg.get("model") or "claude-sonnet-4-20250514"
        self.max_tokens = cfg.get("max_tokens", 4096)
        self.timeout = cfg.get("timeout", 60)
        self.max_retries = cfg.get("max_retries", 3)

    def chat(self, messages: list, system: str = None, temperature: float = 0.3) -> str:
        """Send chat request with retry logic. Returns response text."""
        self._reload()  # Always pick up latest config from frontend
        if not self.base_url:
            raise RuntimeError("LLM base_url not configured. Please set it in the web UI Settings page.")
        if not self.api_key:
            raise RuntimeError("LLM api_key not configured. Please set it in the web UI Settings page.")
        for attempt in range(1, self.max_retries + 1):
            try:
                return self._call_anthropic(messages, system, temperature)
            except Exception as e:
                _log.warning(f"LLM attempt {attempt}/{self.max_retries} failed: {e}")
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
                else:
                    _log.error(f"LLM call failed after {self.max_retries} attempts: {e}")
                    raise

    def _call_anthropic(self, messages, system, temperature):
        base = self.base_url
        if base.endswith("/v1"):
            base = base[:-3]
        url = f"{base}/v1/messages"
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            body["system"] = system

        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("x-api-key", self.api_key)
        req.add_header("anthropic-version", "2023-06-01")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code}: {error_body[:300]}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Connection error: {e.reason}")

        if "content" in result and result["content"]:
            return "".join(b.get("text", "") for b in result["content"] if b.get("type") == "text")
        if "error" in result:
            raise RuntimeError(f"API error: {result['error']}")
        return ""

    def extract_json(self, text: str) -> dict:
        """Extract JSON from LLM response, handling markdown code blocks."""
        text = text.strip()
        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Try extracting from ```json ... ``` blocks
        import re
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        # Try finding first { ... } or [ ... ]
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass
        return {}


# Singleton
_client = None

def get() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def test_connection(base_url=None, api_key=None, model=None) -> dict:
    """Test LLM connection with optional overrides. Returns {ok, message, latency_ms}."""
    import time as _time
    cfg = config.get("llm") or {}
    url = (base_url or cfg.get("base_url") or "").rstrip("/")
    key = api_key or cfg.get("api_key") or ""
    mdl = model or cfg.get("model") or ""

    if not url:
        return {"ok": False, "message": "未配置 API 地址 (Base URL)", "latency_ms": 0}
    if not key:
        return {"ok": False, "message": "未配置 API 密钥 (API Key)", "latency_ms": 0}
    if not mdl:
        return {"ok": False, "message": "未配置模型名称", "latency_ms": 0}

    # Strip trailing /v1 if present to avoid double /v1/v1
    if url.endswith("/v1"):
        url = url[:-3]

    body = json.dumps({
        "model": mdl,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "Hi"}],
    }).encode("utf-8")
    req = urllib.request.Request(f"{url}/v1/messages", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", key)
    req.add_header("anthropic-version", "2023-06-01")

    t0 = _time.time()
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        latency = int((_time.time() - t0) * 1000)
        if "content" in result:
            return {"ok": True, "message": f"Connected to {mdl}", "latency_ms": latency}
        return {"ok": False, "message": f"Unexpected response: {str(result)[:200]}", "latency_ms": latency}
    except urllib.error.HTTPError as e:
        latency = int((_time.time() - t0) * 1000)
        err = e.read().decode("utf-8", errors="replace")[:200]
        return {"ok": False, "message": f"HTTP {e.code}: {err}", "latency_ms": latency}
    except urllib.error.URLError as e:
        latency = int((_time.time() - t0) * 1000)
        return {"ok": False, "message": f"Connection failed: {e.reason}", "latency_ms": latency}
    except Exception as e:
        latency = int((_time.time() - t0) * 1000)
        return {"ok": False, "message": str(e)[:200], "latency_ms": latency}

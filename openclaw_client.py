from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Dict, List


def _sanitize_session_id(session_id: str) -> str:
    s = (session_id or "").strip()
    if not s:
        return "xianyu_session"
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", s)
    return s[:80] if len(s) > 80 else s


def _json_from_mixed_stdout(stdout: str) -> Dict[str, Any]:
    """`openclaw --json` may print warnings before JSON."""
    s = (stdout or "").strip()
    if not s:
        return {}
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    return json.loads(s[start : end + 1])


def call_openclaw_final(prompt: str, session_id: str = "") -> str:
    """Call OpenClaw CLI and return the final text to send."""
    msg = (prompt or "").strip()
    if not msg:
        return ""

    sid = _sanitize_session_id(session_id)

    # Open-source safe defaults: rely on PATH unless explicitly configured.
    openclaw_script = (os.getenv("OPENCLAW_BIN") or "").strip() or "openclaw"
    node_bin = (os.getenv("NODE_BIN") or "").strip() or "node"

    cmd: List[str] = [
        node_bin,
        openclaw_script,
        "agent",
        "--session-id",
        sid,
        "--message",
        msg,
        "--json",
    ]

    agent_id = (os.getenv("OPENCLAW_AGENT_ID") or "").strip()
    if agent_id:
        cmd.extend(["--agent", agent_id])

    timeout = int(os.getenv("OPENCLAW_TIMEOUT_SEC", "120") or "120")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "").strip()[:800])

    data = _json_from_mixed_stdout(p.stdout or "")
    if not data:
        raise RuntimeError((p.stdout or "").strip()[:800])

    result = data.get("result") or {}
    payloads = result.get("payloads") or []
    texts: List[str] = []
    for it in payloads:
        if isinstance(it, dict):
            t = (it.get("text") or "").strip()
            if t:
                texts.append(t)
    return "\n".join(texts).strip()


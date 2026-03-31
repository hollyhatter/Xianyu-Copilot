#!/usr/bin/env python3
"""
Low-token local poller:
- Each run checks unread badges in Xianyu Web IM (DOM-only if already on IM page).
- If there are unread items, call OpenClaw to generate reply text.
- Send via the attached Chrome (remote debugging).

Important: this script is designed to be triggered by launchd (or cron) frequently,
but with a built-in sleep-window and jitter to avoid mechanical behavior patterns.
"""
from __future__ import annotations

import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

# Project root resolved relative to this file (open-source friendly).
ROOT = Path(__file__).resolve().parents[1]


def _dbg(msg: str) -> None:
    # Debug log is opt-in; default is silent to stdout to avoid machine parsing issues.
    if os.getenv("XIANYU_POLL_DEBUG", "0").strip().lower() not in ("1", "true", "yes"):
        return
    try:
        log_path = Path(os.getenv("XIANYU_POLL_DEBUG_LOG", str(ROOT / "xianyu_poller.debug.log")))
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{ts} {msg.rstrip()}\n")
    except Exception:
        pass


def _chrome_ready(port: int) -> bool:
    # Avoid adding extra deps; use curl if present.
    import subprocess

    rc = subprocess.run(
        ["/usr/bin/curl", "-sS", f"http://127.0.0.1:{port}/json/version"],
        capture_output=True,
        text=True,
        timeout=2,
    ).returncode
    return rc == 0


def _load_prompts() -> str:
    prompt_dir = ROOT / "prompts"
    parts: list[str] = []
    for name in ("classify_prompt.txt", "price_prompt.txt", "tech_prompt.txt", "default_prompt.txt"):
        p = prompt_dir / name
        if p.exists():
            parts.append(f"\n\n### {name}\n" + p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts).strip()


def _call_openclaw(prompt: str, session_id: str) -> str:
    sys.path.insert(0, str(ROOT))
    from openclaw_client import call_openclaw_final  # type: ignore

    return (call_openclaw_final(prompt, session_id=session_id) or "").strip()


def main() -> int:
    os.chdir(ROOT)

    # Sleep window: 2:00 - 5:00 (simulate human sleeping).
    hour = datetime.now().hour
    if 2 <= hour < 5:
        _dbg(f"sleep_window exit hour={hour}")
        return 0

    # Jitter to break launchd fixed interval pattern.
    jitter_sec = random.randint(2, 12)
    _dbg(f"jitter_sleep {jitter_sec}s")
    time.sleep(jitter_sec)

    port = int(os.getenv("CHROME_DEBUG_PORT", "9222") or "9222")
    if not _chrome_ready(port):
        _dbg("skip chrome_not_ready")
        return 0

    sys.path.insert(0, str(ROOT))
    from browser_engine import XianyuBrowserEngine  # type: ignore
    from context_manager import ChatContextManager  # type: ignore

    # DOM check (no OpenClaw unless needed).
    try:
        eng = XianyuBrowserEngine(port=port)
        try:
            items = eng.scrape_unread_items() or []
        finally:
            eng.close()
    except Exception as e:
        _dbg(f"check_error {type(e).__name__}:{str(e)[:220]}")
        return 0

    if not items:
        _dbg("items empty")
        return 0

    mgr = ChatContextManager()
    prompts = _load_prompts()
    item_desc = os.getenv("XIANYU_ITEM_DESC", "（你的商品/服务名称）")
    seen_run: set[str] = set()

    for it in items:
        buyer = str(it.get("chat_id") or "").strip()
        buyer = (buyer.splitlines()[0] if buyer else "").strip()
        preview = str(it.get("preview") or "").strip()
        fp = str(it.get("fingerprint") or "").strip()

        if not buyer:
            continue
        run_key = fp or f"{buyer}\0{preview}\0{it.get('unread')}"
        if run_key in seen_run:
            continue
        seen_run.add(run_key)

        _dbg(f"item buyer={buyer} unread={it.get('unread')} preview={preview[:80]}")

        # Compliance: only trigger strict statement when clearly sensitive.
        is_sensitive = any(k in preview for k in ("考场", "考试", "中考", "高考", "作弊", "违规", "监考"))
        compliance_rule = (
            "【合规声明】仅当买家明确提到考试/考场/作弊/违规等敏感关键词时，才输出严正声明；否则不要主动提这些。\n"
            if is_sensitive
            else "【合规声明】本次不要输出任何严正声明/禁止条款，只回答卖点与购买/发货/售后即可。\n"
        )

        user_prompt = (
            "你是闲鱼卖家自动回复助手。\n"
            f"我售卖的商品是：{item_desc}。\n"
            "请严格遵守下方话术与规则（禁止引导微信/QQ/线下）。\n"
            "【输出格式强约束】你只能输出“最终要发送给买家的文字消息本体”，不允许输出：分类/意图标签、分析过程、引用了哪个prompt、分隔线、标题、Markdown、引号包裹、或任何解释性前缀。\n"
            + compliance_rule
            + "回复要求：1-3 句，礼貌、明确、可直接发送。\n\n"
            f"买家昵称：{buyer}\n"
            f"买家最新消息：{preview}\n\n"
            "【话术规则如下】\n"
            f"{prompts}\n"
        )

        try:
            reply = _call_openclaw(user_prompt, session_id=f"xianyu:{buyer}")
        except Exception as e:
            _dbg(f"openclaw_error buyer={buyer} {type(e).__name__}:{str(e)[:300]}")
            continue
        reply = (reply or "").strip()
        if not reply:
            continue

        try:
            eng2 = XianyuBrowserEngine(port=port)
            try:
                eng2.send_text_to_chat(buyer, reply)
            finally:
                eng2.close()
        except Exception as e:
            _dbg(f"send_fail buyer={buyer} {type(e).__name__}:{str(e)[:240]}")
            continue

        if fp:
            try:
                mgr.mark_browser_unread_seen(fp)
            except Exception as e:
                _dbg(f"mark_seen_fail fp={fp[:12]} {type(e).__name__}:{str(e)[:200]}")

        if os.getenv("XIANYU_REFRESH_AFTER_REPLY", "1").strip().lower() in ("1", "true", "yes"):
            try:
                eng3 = XianyuBrowserEngine(port=port)
                try:
                    eng3.refresh_im_once(ignore_cache=False)
                finally:
                    eng3.close()
            except Exception as e:
                _dbg(f"refresh_after_reply_fail {type(e).__name__}:{str(e)[:200]}")

        time.sleep(float(os.getenv("XIANYU_REPLY_GAP_SEC", "0.8") or "0.8"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


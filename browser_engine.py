"""
Xianyu (Goofish) web IM automation via DrissionPage + Chrome remote debugging.

This is the open-source copy of your local bridge. It is designed to:
- Read unread badges ("red dots"/counts) from the DOM.
- Click a conversation by buyer nickname and send a reply.
- Avoid unnecessary navigation/refresh during checks.

All runtime configuration should be provided via environment variables.
"""
from __future__ import annotations

import hashlib
import os
import random
import time
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    from DrissionPage import ChromiumOptions, ChromiumPage
except ImportError as e:  # pragma: no cover
    ChromiumOptions = None  # type: ignore
    ChromiumPage = None  # type: ignore
    _IMPORT_ERR = e
else:
    _IMPORT_ERR = None


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def fingerprint_unread(chat_id: str, preview: str, unread: int | None = None) -> str:
    """Hash a DOM event for local bookkeeping/debugging."""
    u = "" if unread is None else str(int(unread))
    raw = f"{chat_id}\0{preview}\0{u}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()


class XianyuBrowserEngine:
    """Attach to an already logged-in Chrome via remote debugging."""

    def __init__(self, port: int = 9222):
        if _IMPORT_ERR is not None:
            raise RuntimeError("DrissionPage not installed. Run: pip install DrissionPage") from _IMPORT_ERR
        self.port = int(os.getenv("CHROME_DEBUG_PORT", str(port)))
        self.im_url = _env("XIANYU_IM_URL", "https://www.goofish.com/im")
        self.page_timeout = float(_env("XIANYU_PAGE_TIMEOUT", "25"))
        # Don't hard-bind type: get_tab()/get_tabs() may return ChromiumTab.
        self._page: Optional[Any] = None

    def _connect(self) -> ChromiumPage:
        if self._page is not None:
            return self._page
        opts = ChromiumOptions()
        opts.set_local_port(self.port)
        opts.existing_only(True)
        self._page = ChromiumPage(addr_or_opts=opts)
        try:
            self._page.set.timeouts(base=self.page_timeout)
        except Exception:
            pass
        logger.info(f"connected chrome remote debugging port={self.port}")
        return self._page

    def close(self) -> None:
        """Disconnect from Chrome (do NOT quit the whole browser)."""
        if self._page is not None:
            try:
                if _env("XIANYU_ENGINE_QUIT_BROWSER", "0") in ("1", "true", "yes"):
                    self._page.quit()
                else:
                    self._page.disconnect()
            except Exception as e:
                logger.debug(f"disconnect page: {e}")
            self._page = None

    def _is_im_url(self, url: str) -> bool:
        u = (url or "").strip()
        if not u:
            return False
        return ("goofish.com/im" in u) or ("goofish.com/message" in u) or ("/im" in u and "goofish.com" in u)

    def _looks_blank_list(self, page: Any) -> bool:
        """Detect abnormal blank UI state."""
        try:
            return bool(
                page.run_js(
                    """
                    (function () {
                      const t = (document.body && document.body.innerText) ? document.body.innerText : '';
                      const hasEmpty = t.includes('尚未选择任何联系人') || t.includes('快点左侧列表聊起来吧');
                      const hasConv = !!document.querySelector('#conv-list-scrollable')
                        || document.querySelectorAll('div[class*="conversation-item"]').length > 0
                        || document.querySelectorAll('div[style*="font-weight: 500"]').length > 0;
                      return hasEmpty && !hasConv;
                    })()
                    """
                )
            )
        except Exception:
            return False

    def _ensure_im(self) -> ChromiumPage:
        """Silent-by-default: don't navigate/refresh on normal checks."""
        page = self._connect()
        try:
            cur_url = getattr(page, "url", "") or ""
        except Exception:
            cur_url = ""

        need_nav = (not self._is_im_url(cur_url)) or self._looks_blank_list(page)
        if need_nav:
            page.get(self.im_url)
            try:
                page.wait.doc_loaded(timeout=int(float(_env("XIANYU_DOC_LOAD_TIMEOUT", "25"))))
            except Exception:
                pass
            time.sleep(float(_env("XIANYU_IM_LOAD_WAIT", "2.5")))
        else:
            time.sleep(float(_env("XIANYU_IM_SCAN_PAUSE_SEC", "0.2")))

        # Best-effort pick the richest IM tab, without forcing refresh.
        try:
            tabs = page.get_tabs(url="goofish.com/im")  # type: ignore[attr-defined]
        except Exception:
            tabs = []
        if tabs:
            best = None
            best_score = -1
            for t in tabs[:6]:
                try:
                    score = int(
                        t.run_js(
                            "return document.querySelectorAll('div[style*=\"font-weight: 500\"]').length + "
                            "document.querySelectorAll('sup[title]').length + "
                            "document.querySelectorAll('[class*=ant-badge-count],[class*=ant-scroll-number]').length;"
                        )
                        or 0
                    )
                except Exception:
                    score = 0
                if score > best_score:
                    best_score = score
                    best = t
            if best is not None and best_score > 0:
                return best  # type: ignore[return-value]

        return page

    def scrape_unread_items(self) -> List[Dict[str, Any]]:
        """Return unread conversation items from DOM red dots/counts."""
        page = self._ensure_im()
        include_zero = _env("XIANYU_INCLUDE_ZERO_UNREAD", "0") in ("1", "true", "yes")

        def _safe_int(s: str) -> int:
            s = (s or "").strip()
            return int(s) if s.isdigit() else 0

        def _find_row(ele) -> Any:
            try:
                row = ele.parent(".ant-dropdown-trigger", timeout=0)
                if row:
                    return row
            except Exception:
                pass
            cur = ele
            for _ in range(20):
                try:
                    cur = cur.parent()
                except Exception:
                    break
                if not cur:
                    break
                try:
                    cls = (cur.attr("class") or "").strip()
                except Exception:
                    cls = ""
                if "ant-dropdown-trigger" in cls:
                    return cur
            return None

        def _extract_nickname(row) -> str:
            try:
                nick = row.ele('xpath:.//div[contains(@style,"font-weight: 500")]', timeout=0)
                if nick and (nick.text or "").strip():
                    return (nick.text or "").strip()
            except Exception:
                pass
            try:
                t = (row.text or "").strip().splitlines()
                return t[0].strip() if t else ""
            except Exception:
                return ""

        def _extract_preview(row) -> str:
            try:
                msg = row.ele(
                    'xpath:.//div[contains(@style,"color: rgb(163, 163, 163)") and contains(@style,"font-size: 12px")]',
                    timeout=0,
                )
                if msg and (msg.text or "").strip():
                    return (msg.text or "").strip()
            except Exception:
                pass
            try:
                cands = row.eles('xpath:.//div[contains(@style,"color: rgb(163, 163, 163)")]')
            except Exception:
                cands = []
            for c in cands or []:
                try:
                    t = (c.text or "").strip()
                except Exception:
                    continue
                if not t:
                    continue
                if "前" in t and ("分钟" in t or "小时" in t or "天" in t):
                    continue
                return t
            return ""

        # Fast JS extraction for known list container.
        try:
            js_items = page.run_js(
                """
                (function () {
                  const out = [];
                  const norm = (s) => (s || '').trim();
                  const root = document.querySelector('#conv-list-scrollable') || document;
                  const rows = root.querySelectorAll('div[class*="conversation-item"]');
                  for (const row of rows) {
                    const supCount = row.querySelector('sup[title][class*="ant-badge-count"], sup[title][class*="ant-scroll-number"]');
                    const title = supCount ? norm(supCount.getAttribute('title') || '') : '';
                    const unreadCount = title && /^\\d+$/.test(title) ? parseInt(title, 10) : 0;
                    const supDot = row.querySelector('sup[class*="ant-badge-dot"], sup[class*="ant-scroll-number"][class*="dot"], sup[class*="badge-dot"]');
                    const unread = unreadCount > 0 ? unreadCount : (supDot ? 1 : 0);
                    if (unread <= 0) continue;
                    const nick = row.querySelector('div[style*="font-weight: 500"] div') || row.querySelector('div[style*="font-weight: 500"]');
                    const preview = row.querySelector('div[style*="font-size: 12px"][style*="color: rgb(163, 163, 163)"]');
                    let nickText = norm(nick ? nick.textContent : '');
                    if (nickText && nickText.includes('\\n')) nickText = norm(nickText.split('\\n')[0]);
                    if (!nickText || nickText === '通知消息' || nickText === '消息') continue;
                    out.push({ chat_id: nickText, preview: norm(preview ? preview.textContent : ''), unread: unread });
                  }
                  return out;
                })()
                """
            )
            if isinstance(js_items, list) and js_items:
                out: List[Dict[str, Any]] = []
                for it in js_items:
                    if not isinstance(it, dict):
                        continue
                    cid = str(it.get("chat_id") or "").strip()
                    if not cid:
                        continue
                    preview = str(it.get("preview") or "").strip()[:500]
                    unread = int(it.get("unread") or 0)
                    if unread <= 0 and not include_zero:
                        continue
                    out.append(
                        {
                            "chat_id": cid,
                            "preview": preview,
                            "unread": unread,
                            "fingerprint": fingerprint_unread(cid, preview, unread),
                        }
                    )
                if out:
                    return out
        except Exception as e:
            logger.debug(f"js scrape failed, fallback to frames: {e}")

        frames: List[Any] = [page]
        try:
            frames.extend(list(page.get_frames()))
        except Exception:
            pass

        seen = set()
        out: List[Dict[str, Any]] = []
        for f in frames:
            try:
                badges = []
                try:
                    badges.extend(f.eles(".ant-badge-count", timeout=0))
                except Exception:
                    pass
                try:
                    badges.extend(f.eles('[class*="ant-badge-dot"]', timeout=0))
                except Exception:
                    pass
                try:
                    badges.extend(f.eles('[class*="ant-badge-count"]', timeout=0))
                except Exception:
                    pass
                try:
                    badges.extend(f.eles("xpath://sup[@title]", timeout=0))
                except Exception:
                    pass
                if not badges:
                    continue

                for b in badges:
                    try:
                        cls = (b.attr("class") or "") if hasattr(b, "attr") else ""
                        if "ant-badge-dot" in cls:
                            unread = 1
                        else:
                            unread = _safe_int(b.attr("title") or "") or _safe_int(b.text or "")
                    except Exception:
                        unread = 0
                    if unread <= 0 and not include_zero:
                        continue
                    row = _find_row(b)
                    if not row:
                        continue
                    nickname = _extract_nickname(row)
                    if not nickname:
                        continue
                    nickname = (nickname.splitlines()[0] if nickname else "").strip()
                    if nickname in seen:
                        continue
                    seen.add(nickname)
                    preview = _extract_preview(row)[:500]
                    out.append(
                        {
                            "chat_id": nickname,
                            "preview": preview,
                            "unread": max(1, unread) if unread > 0 else 0,
                            "fingerprint": fingerprint_unread(nickname, preview, unread),
                        }
                    )
            except Exception:
                continue
        return out

    def refresh_im_once(self, ignore_cache: bool = False) -> None:
        """Refresh the IM page once (optional)."""
        page = self._ensure_im()
        try:
            page.refresh(ignore_cache=ignore_cache)
            try:
                page.wait.doc_loaded(timeout=int(float(_env("XIANYU_DOC_LOAD_TIMEOUT", "25"))))
            except Exception:
                pass
            time.sleep(float(_env("XIANYU_IM_LOAD_WAIT", "2.5")))
        except Exception as e:
            logger.debug(f"refresh_im_once failed: {e}")

    def send_text_to_chat(self, chat_id: str, text: str) -> None:
        page = self._ensure_im()
        nickname = (chat_id or "").strip()
        if not nickname:
            raise ValueError("chat_id is empty (current strategy: chat_id = buyer nickname)")
        msg = (text or "").strip()
        if not msg:
            raise ValueError("message is empty")

        frames: List[Any] = [page]
        try:
            frames.extend(list(page.get_frames()))
        except Exception:
            pass

        clicked = False
        for f in frames:
            try:
                el = f.ele(f"text:{nickname}", timeout=0)
                if not el:
                    continue
                try:
                    row = el.parent(".ant-dropdown-trigger", timeout=0) or el.parent()
                except Exception:
                    row = None
                try:
                    (row or el).click()
                    clicked = True
                    break
                except Exception:
                    continue
            except Exception:
                continue

        if not clicked:
            raise RuntimeError(f"cannot find conversation by nickname: {nickname}")

        time.sleep(float(_env("XIANYU_AFTER_CLICK_WAIT", "0.8")))

        send_ok = False
        for ctx in [page, *frames]:
            try:
                if _try_focus_input_and_send(ctx, msg):
                    send_ok = True
                    break
            except Exception:
                continue

        if not send_ok:
            raise RuntimeError("send failed: cannot find input or cannot submit")


def _try_focus_input_and_send(page: Any, msg: str) -> bool:
    def _human_pause(a: float = 0.30, b: float = 1.20) -> None:
        time.sleep(random.uniform(a, b))

    def _human_type(text: str) -> None:
        interval = float(os.getenv("XIANYU_TYPING_INTERVAL_SEC", "0.035") or "0.035")
        jitter = float(os.getenv("XIANYU_TYPING_INTERVAL_JITTER_SEC", "0.020") or "0.020")
        per_char = max(0.0, random.uniform(max(0.0, interval - jitter), interval + jitter))
        page.actions.type(text, interval=per_char)

    def _input_has_text(prefix: str) -> bool:
        try:
            return bool(
                page.run_js(
                    """
                    (function(pfx){
                      const p = (pfx||'').trim();
                      const el = document.activeElement;
                      const read = (x) => {
                        if (!x) return '';
                        if (typeof x.value === 'string') return x.value;
                        if (x.isContentEditable) return x.innerText || x.textContent || '';
                        return x.innerText || x.textContent || '';
                      };
                      const s = (read(el) || '').trim();
                      if (!s) return false;
                      if (!p) return true;
                      return s.includes(p);
                    })(arguments[0])
                    """,
                    prefix,
                )
            )
        except Exception:
            return False

    sel_in = _env("XIANYU_SEL_INPUT", "") or 'css:textarea[placeholder*="请输入消息"]'
    sel_send = _env("XIANYU_SEL_SEND", "")
    fast = _env("XIANYU_FAST_INPUT", "0") in ("1", "true", "yes")

    box = None
    try:
        box = page.ele(sel_in, timeout=8)
    except Exception:
        box = None
    if box:
        box.click()
        _human_pause(0.35, 1.05)
        try:
            box.clear()
        except Exception:
            pass
        _human_pause(0.30, 0.95)
        if fast:
            try:
                box.input(msg)
            except Exception:
                _human_type(msg)
        else:
            _human_type(msg)
        _human_pause(0.35, 1.10)
        try:
            page.run_js(
                """
                (function () {
                  const el = document.querySelector('textarea[placeholder*="请输入消息"]') || document.activeElement;
                  if (!el) return false;
                  const ev = (t) => new KeyboardEvent(t, {bubbles:true, key:'Enter', code:'Enter', keyCode:13});
                  el.dispatchEvent(ev('keydown')); el.dispatchEvent(ev('keyup'));
                  return true;
                })()
                """
            )
        except Exception:
            pass

        _human_pause(0.30, 1.00)
        if _input_has_text(msg.strip()[:12]):
            for s in (sel_send, "text:发送", "text:发 送", "xpath://button[contains(.,'发送')]"):
                if not s:
                    continue
                try:
                    btn = page.ele(s, timeout=1.5)
                    if btn:
                        _human_pause(0.30, 1.05)
                        btn.click()
                        _human_pause(0.35, 1.10)
                        break
                except Exception:
                    continue
        return not _input_has_text(msg.strip()[:12])

    # Fallback locators
    for loc in (
        'css:textarea[placeholder*="请输入消息"]',
        "tag:textarea",
        "xpath://div[@contenteditable='true']",
        "xpath://*[@role='textbox']",
    ):
        try:
            box = page.ele(loc, timeout=3)
        except Exception:
            box = None
        if box:
            try:
                box.click()
                _human_pause(0.20, 0.75)
                box.clear()
            except Exception:
                pass
            _human_pause(0.30, 0.95)
            if fast:
                try:
                    box.input(msg)
                except Exception:
                    _human_type(msg)
            else:
                _human_type(msg)
            _human_pause(0.35, 1.10)
            page.run_js(
                """
                const el = document.activeElement;
                if (el && (el.tagName==='TEXTAREA' || el.isContentEditable)) {
                  const ev = (t) => new KeyboardEvent(t, {bubbles:true, key:'Enter', code:'Enter', keyCode:13});
                  el.dispatchEvent(ev('keydown'));
                  el.dispatchEvent(ev('keyup'));
                }
                """
            )
            _human_pause(0.30, 1.00)
            if _input_has_text(msg.strip()[:12]):
                for s in (sel_send, "text:发送", "text:发 送", "xpath://button[contains(.,'发送')]"):
                    if not s:
                        continue
                    try:
                        btn = page.ele(s, timeout=1.5)
                        if btn:
                            _human_pause(0.30, 1.05)
                            btn.click()
                            _human_pause(0.35, 1.10)
                            break
                    except Exception:
                        continue
            return not _input_has_text(msg.strip()[:12])
    return False


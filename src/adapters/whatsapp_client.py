"""
WhatsApp Web client via Playwright.

Same idea as wat-a-bot's whatsapp-web.js + LocalAuth:
- Persistent Chromium profile under config/wa-auth/session-<WA_PHONE>
- First run: scan QR (Linked devices → Link a device)
- Later runs: reuse session for that account phone
"""

from __future__ import annotations

import hashlib
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page, sync_playwright

from src.utils.config import config

WHATSAPP_URL = "https://web.whatsapp.com/"

# Header / list noise that must never be treated as a contact name
_STATUS_NAMES = {
    "online",
    "typing…",
    "typing...",
    "typing",
    "recording…",
    "recording...",
    "recording audio…",
    "unknown",
    "business account",
    "you",
}


def _is_status_name(name: str) -> bool:
    low = (name or "").strip().lower()
    if not low:
        return True
    if low in _STATUS_NAMES:
        return True
    if low.startswith("last seen"):
        return True
    if low.startswith("typing"):
        return True
    if "click here" in low or "contact info" in low:
        return True
    return False


@dataclass
class IncomingMessage:
    chat_name: str
    text: str
    fingerprint: str
    is_group: bool = False


def _maybe_migrate_legacy_session(session_dir: Path) -> None:
    """Reuse root .whatsapp_data if the new LocalAuth-style folder is empty/missing."""
    legacy = config.root / ".whatsapp_data"
    if session_dir.exists() and any(session_dir.iterdir()):
        return
    if not legacy.exists() or not any(legacy.iterdir()):
        return
    session_dir.parent.mkdir(parents=True, exist_ok=True)
    if session_dir.exists():
        return
    print(f"[wa] Migrating legacy session {legacy} -> {session_dir}")
    shutil.copytree(legacy, session_dir)


class WhatsAppClient:
    """Persistent WhatsApp Web session using Playwright (wat-a-bot LocalAuth equivalent)."""

    def __init__(
        self,
        headless: bool | None = None,
        user_data_dir: Path | None = None,
        poll_interval_sec: float | None = None,
        playwright=None,
        phone: str | None = None,
    ) -> None:
        self.headless = config.get_wa_headless() if headless is None else headless
        self.phone = phone if phone is not None else (config.get_wa_phone() or config.get_wa_client_id())
        self.user_data_dir = user_data_dir or config.get_wa_session_dir()
        self.poll_interval_sec = (
            config.get_poll_interval_sec() if poll_interval_sec is None else poll_interval_sec
        )
        self._external_pw = playwright is not None
        self._playwright = playwright
        self._context = None
        self._page: Page | None = None
        self._seen: set[str] = set()
        self._list_previews: dict[str, str] = {}
        self._skip_chats: set[str] = set()
        # chat_name -> last inbound text we already answered (stops repeat replies)
        self._replied_to: dict[str, str] = {}
        # Normalized texts we sent (never treat as inbound)
        self._outbound_texts: set[str] = set()
        self._ready = False
        self._poll_count = 0
        self._last_scan_stats: dict[str, int] = {}

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("WhatsAppClient is not started. Call start() first.")
        return self._page

    def start(self, login_timeout_sec: int = 300) -> None:
        if self._page is not None:
            return

        _maybe_migrate_legacy_session(self.user_data_dir)
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        print(f"[wa] Account phone={self.phone}")
        print(f"[wa] Starting browser (session={self.user_data_dir})...")
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        # Open WhatsApp Web — same connect path as wat-a-bot (QR / saved session)
        self._page.goto(WHATSAPP_URL, wait_until="domcontentloaded")
        self._wait_until_ready(login_timeout_sec)
        # Snapshot list previews so we only react to NEW list changes after start
        try:
            for row in self._chat_list_rows():
                self._list_previews[row["name"]] = row.get("preview") or ""
            print(f"[wa] Tracked {len(self._list_previews)} chat list previews.")
        except Exception as exc:
            print(f"[wa] preview snapshot failed: {exc}")
        self._ready = True
        print(f"[wa] Ready - listening on account {self.phone}.")
        print(
            "[wa] Policy: personal DMs with unread badge OR new chat-list preview "
            "(groups ignored). Keep browsers open. Send a NEW message AFTER bot starts."
        )

    def _wait_until_ready(self, timeout_sec: int) -> None:
        print(
            f"[wa] Scan QR with phone {self.phone} "
            "(WhatsApp -> Linked devices -> Link a device)..."
        )
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            pane = self.page.locator(
                "#pane-side, [data-testid='chat-list'], div[aria-label='Chat list']"
            )
            if pane.count() > 0 and pane.first.is_visible():
                print("[wa] Logged in.")
                return
            qr = self.page.locator("canvas[aria-label*='QR'], div[data-ref] canvas, canvas")
            if qr.count() > 0:
                print("[wa] Waiting for QR scan...", end="\r")
            self.page.wait_for_timeout(1500)
        raise TimeoutError("Timed out waiting for WhatsApp Web login / QR scan.")

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            self._context = None
            self._page = None
            self._ready = False
            if self._playwright is not None and not self._external_pw:
                self._playwright.stop()
                self._playwright = None
            print("[wa] Closed.")

    def _fingerprint(self, chat_name: str, text: str) -> str:
        raw = f"{chat_name}||{self._normalize_text(text)}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_text(text: str) -> str:
        t = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n{2,}", "\n", t)
        return t.strip().lower()

    def _already_answered(self, chat_name: str, text: str) -> bool:
        key = self._normalize_text(text)
        if not key:
            return False
        prev = self._replied_to.get(chat_name)
        if prev and prev == key:
            return True
        fp = self._fingerprint(chat_name, text)
        return fp in self._seen

    def mark_handled(self, message: IncomingMessage) -> None:
        """Call after a successful reply so the same inbound text is never answered again."""
        key = self._normalize_text(message.text)
        self._replied_to[message.chat_name] = key
        self._seen.add(message.fingerprint or self._fingerprint(message.chat_name, message.text))
        # Also store preview fingerprints so list fallback cannot retrigger
        preview = self._list_previews.get(message.chat_name)
        if preview:
            self._seen.add(self._fingerprint(message.chat_name, preview))
        print(f"[wa] marked handled: {message.chat_name!r}")

    def remember_outbound(self, text: str) -> None:
        """Remember a message we sent so we never feed it back into ChatGPT."""
        key = self._normalize_text(text)
        if key:
            self._outbound_texts.add(key)
            if len(self._outbound_texts) > 500:
                self._outbound_texts = set(list(self._outbound_texts)[-300:])

    def _is_group_chat(self) -> bool:
        try:
            header = self.page.locator("header").last
            if header.count() == 0:
                return False
            sub = header.locator("span[title], span[dir='auto']").all_inner_texts()
            joined = " ".join(sub).lower()
            if re.search(r"\d+\s+(participants|members)", joined):
                return True
            if "group" in joined and "click here" in joined:
                return True
        except Exception:
            pass
        return False

    def _active_chat_name(self) -> str:
        try:
            name = self.page.evaluate(
                """() => {
                  const main = document.querySelector('#main');
                  if (!main) return '';
                  const header = main.querySelector('header');
                  if (!header) return '';
                  // Prefer the conversation title, not subtitle ("click here for contact info")
                  const titled = [...header.querySelectorAll('span[title]')].map(el => ({
                    title: (el.getAttribute('title') || '').trim(),
                    text: (el.textContent || '').trim(),
                  })).filter(x => x.title || x.text);
                  for (const x of titled) {
                    const t = x.title || x.text;
                    const low = t.toLowerCase();
                    if (!t) continue;
                    if (low.includes('click here')) continue;
                    if (low.includes('contact info')) continue;
                    if (low === 'business account') continue;
                    if (low === 'online') continue;
                    if (low.startsWith('typing')) continue;
                    if (low.startsWith('last seen')) continue;
                    if (low.includes('recording')) continue;
                    if (low.includes('participants') || low.includes('members')) continue;
                    return t;
                  }
                  const auto = header.querySelector('span[dir="auto"]');
                  if (auto) {
                    const t = (auto.textContent || '').trim();
                    if (t && !/click here|contact info|business account|^online$|^typing|^last seen|recording/i.test(t)) return t;
                  }
                  return '';
                }"""
            )
            if name and str(name).strip():
                return str(name).strip()
        except Exception:
            pass
        try:
            spans = self.page.locator("#main header span[title]")
            n = spans.count()
            for i in range(n):
                title = (spans.nth(i).get_attribute("title") or spans.nth(i).inner_text() or "").strip()
                low = title.lower()
                if not title:
                    continue
                if "click here" in low or "contact info" in low or low == "business account":
                    continue
                if _is_status_name(title):
                    continue
                return title
        except Exception:
            pass
        return "unknown"

    def _last_incoming_text(self) -> str | None:
        """Best-effort read of last incoming bubble text in the open chat."""
        try:
            text = self.page.evaluate(
                """() => {
                  const main = document.querySelector('#main');
                  if (!main) return null;

                  const bad = (t) => {
                    const s = (t || '').trim();
                    if (!s) return true;
                    const low = s.toLowerCase();
                    if (low === 'today' || low === 'yesterday') return true;
                    if (/^\\d{1,2}:\\d{2}\\s?(am|pm)?$/i.test(s)) return true;
                    if (low.includes('click here')) return true;
                    if (low.includes('tap to')) return true;
                    if (low.includes('message itself deleted')) return true;
                    return false;
                  };

                  const pickLast = (nodes) => {
                    for (let i = nodes.length - 1; i >= 0; i--) {
                      const t = (nodes[i].innerText || nodes[i].textContent || '').trim();
                      if (!bad(t)) return t;
                    }
                    return null;
                  };

                  // 1) Classic WhatsApp Web incoming bubbles
                  let hit = pickLast([
                    ...main.querySelectorAll('div.message-in span.selectable-text'),
                    ...main.querySelectorAll('div.message-in span.copyable-text'),
                    ...main.querySelectorAll('div.message-in div.copyable-text'),
                    ...main.querySelectorAll('div.message-in span[data-testid="conversation-text"]'),
                  ]);
                  if (hit) return hit;

                  // 2) Any selectable-text not inside an outgoing bubble
                  hit = pickLast(
                    [...main.querySelectorAll('span.selectable-text, span.copyable-text, span[data-testid="conversation-text"]')]
                      .filter(el => !el.closest('div.message-out') && !el.closest('footer') && !el.closest('header'))
                  );
                  if (hit) return hit;

                  // 3) data-pre-plain-text rows (often on copyable-text wrappers)
                  const prefs = [...main.querySelectorAll('[data-pre-plain-text]')];
                  for (let i = prefs.length - 1; i >= 0; i--) {
                    const el = prefs[i];
                    if (el.closest('div.message-out')) continue;
                    const t = (el.innerText || el.textContent || '').trim();
                    if (!bad(t)) return t;
                  }

                  // 4) Messages with data-id (incoming usually true_... or ! from-me patterns)
                  const rows = [...main.querySelectorAll('div[data-id]')];
                  for (let i = rows.length - 1; i >= 0; i--) {
                    const row = rows[i];
                    const id = row.getAttribute('data-id') || '';
                    if (row.classList.contains('message-out')) continue;
                    if (/^false_/.test(id)) continue; // often own messages
                    if (row.querySelector('div.message-out')) continue;
                    const span = row.querySelector('span.selectable-text, span.copyable-text, span[dir="ltr"], span[dir="auto"]');
                    if (!span) continue;
                    const t = (span.innerText || span.textContent || '').trim();
                    if (!bad(t)) return t;
                  }

                  // 5) Last focusable chat row text blob (last resort)
                  const focus = [...main.querySelectorAll('div.focusable-list-item, div[role="row"]')];
                  for (let i = focus.length - 1; i >= 0; i--) {
                    const row = focus[i];
                    if (row.closest('footer') || row.closest('header')) continue;
                    if (row.querySelector('div.message-out')) continue;
                    const t = (row.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
                    // drop time-only trailing lines
                    while (t.length && /^\\d{1,2}:\\d{2}/.test(t[t.length - 1])) t.pop();
                    const body = t.join(' ').trim();
                    if (!bad(body) && body.length > 0 && body.length < 4000) return body;
                  }
                  return null;
                }"""
            )
            if text:
                return str(text).strip()
        except Exception as exc:
            print(f"[wa] text extract error: {exc}")

        selectors = [
            "#main div.message-in span.selectable-text",
            "#main div.message-in span.copyable-text",
            "#main span.selectable-text",
            "#main [data-pre-plain-text]",
            "#main div[data-id] span.selectable-text",
        ]
        for sel in selectors:
            try:
                nodes = self.page.locator(sel)
                n = nodes.count()
                if n > 0:
                    t = nodes.nth(n - 1).inner_text().strip()
                    if t and t.lower() not in {"today", "yesterday"}:
                        return t
            except Exception:
                continue
        return None

    def _preview_for_name(self, name: str) -> str | None:
        """Chat-list last-message preview for a contact (fallback when bubbles unreadable)."""
        preview = (self._list_previews.get(name) or "").strip()
        if preview:
            return preview
        for row in self._chat_list_rows():
            if row.get("name") == name:
                p = (row.get("preview") or "").strip()
                if p:
                    self._list_previews[name] = p
                    return p
        return None

    def _chat_list_rows(self) -> list[dict]:
        """Left-pane chats: name, preview, unread flag."""
        try:
            rows = self.page.evaluate(
                """() => {
                  const out = [];
                  const seen = new Set();
                  const pane = document.querySelector('#pane-side');
                  if (!pane) return out;

                  const isUnread = (row, label) => {
                    if (/\\bunread\\b/i.test(label)) return true;
                    if (/\\d+\\s+unread/i.test(label)) return true;
                    if (row.querySelector('[data-testid="icon-unread-count"]')) return true;
                    if (row.querySelector('[data-testid="unread-count"]')) return true;
                    if (row.querySelector('[data-testid="icon-unread-mention"]')) return true;
                    if (row.querySelector('[data-icon="unread-count"]')) return true;
                    if (row.querySelector('[aria-label*="unread" i]')) return true;
                    // Green / teal unread badge: small numeric pill next to time
                    const badges = row.querySelectorAll('span, div');
                    for (const b of badges) {
                      const t = (b.textContent || '').trim();
                      if (!/^\\d{1,3}$/.test(t)) continue;
                      const al = (b.getAttribute('aria-label') || '').toLowerCase();
                      if (al.includes('unread') || al.includes('mention')) return true;
                      // Typical WA badge is a short digit with no title attribute
                      if (!b.getAttribute('title') && t.length <= 3) {
                        const style = window.getComputedStyle(b);
                        const bg = (style.backgroundColor || '').replace(/\\s/g, '');
                        // WhatsApp unread is often green-ish (approximate)
                        if (/rgba?\\((0|\\d+),\\s?(12[0-9]|13[0-9]|14[0-9]|1[5-9]\\d|2\\d\\d),/.test(bg)) return true;
                        if (bg.includes('37,211,102') || bg.includes('0,168,132') || bg.includes('25,211,102')) return true;
                      }
                    }
                    return false;
                  };

                  const previewFrom = (row, name) => {
                    const lines = (row.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
                    // Prefer a line that is not the contact name and not a bare time
                    for (const line of lines) {
                      if (!line || line === name) continue;
                      if (/^\\d{1,2}:\\d{2}/.test(line)) continue;
                      if (/^(today|yesterday)$/i.test(line)) continue;
                      return line;
                    }
                    return lines.length >= 2 ? lines[1] : '';
                  };

                  // WhatsApp Web DOM changes often — try several row shapes
                  let rowNodes = [...pane.querySelectorAll('[role="listitem"]')];
                  if (rowNodes.length === 0) {
                    rowNodes = [...pane.querySelectorAll('div[data-testid="cell-frame-container"]')];
                  }
                  if (rowNodes.length === 0) {
                    const titles = pane.querySelectorAll('span[title]');
                    for (const titleEl of titles) {
                      const name = (titleEl.getAttribute('title') || '').trim();
                      if (!name || seen.has(name)) continue;
                      const row =
                        titleEl.closest('[role="listitem"]') ||
                        titleEl.closest('div[data-testid="cell-frame-container"]') ||
                        titleEl.closest('div') ||
                        titleEl.parentElement;
                      if (!row) continue;
                      const label = (row.getAttribute('aria-label') || '') + ' ' + (row.innerText || '');
                      if (/\\d+\\s+(participants|members)/i.test(label)) continue;
                      const preview = previewFrom(row, name);
                      const unread = isUnread(row, label);
                      seen.add(name);
                      out.push({ name, preview, unread: !!unread, label: label.slice(0, 200) });
                    }
                    return out;
                  }

                  for (const row of rowNodes) {
                    const label = (row.getAttribute('aria-label') || '') + ' ' + (row.innerText || '');
                    if (/\\d+\\s+(participants|members)/i.test(label)) continue;
                    const titleEl = row.querySelector('span[title]');
                    const name = (titleEl && (titleEl.getAttribute('title') || titleEl.textContent) || '').trim();
                    if (!name || seen.has(name)) continue;
                    const preview = previewFrom(row, name);
                    const unread = isUnread(row, label);
                    seen.add(name);
                    out.push({ name, preview, unread: !!unread, label: label.slice(0, 200) });
                  }
                  return out;
                }"""
            )
            return list(rows or [])
        except Exception as exc:
            print(f"[wa] chat list read error: {exc}")
            return []

    def _looks_like_group_name(self, name: str, label: str = "") -> bool:
        blob = f"{name} {label}".lower()
        if re.search(r"\d+\s+(participants|members)", blob):
            return True
        if "group" in blob and ("click here" in blob or "info" in blob):
            return True
        if re.search(r"[\U0001F300-\U0001FAFF]", name) and len(name) > 18:
            return True
        if name.isupper() and len(name) > 20:
            return True
        group_keywords = (
            "community",
            "discussion",
            "gossip",
            "job opening",
            "world",
            "channel",
            "broadcast",
            "invest",
            "eagles",
        )
        if any(k in blob for k in group_keywords):
            return True
        if " - " in name and len(name) > 22:
            return True
        return False

    def _looks_like_personal_name(self, name: str) -> bool:
        if not name or _is_status_name(name) or self._looks_like_group_name(name):
            return False
        parts = [p for p in re.split(r"\s+", name.strip()) if p]
        if not parts or len(parts) > 4 or len(name) > 40:
            return False
        if re.search(r"[\U0001F300-\U0001FAFF]", name):
            return False
        return True

    def _click_chat_in_list(self, name: str) -> bool:
        """Open a chat by clicking its row in the left list (no search box)."""
        try:
            exact = self.page.locator(f'#pane-side span[title="{name}"]')
            if exact.count() == 0:
                ok = self.page.evaluate(
                    """(name) => {
                      const pane = document.querySelector('#pane-side');
                      if (!pane) return false;
                      for (const el of pane.querySelectorAll('span[title]')) {
                        const t = (el.getAttribute('title') || '').trim();
                        if (t !== name) continue;
                        const row =
                          el.closest('[role="listitem"]') ||
                          el.closest('div[data-testid="cell-frame-container"]');
                        (row || el).click();
                        return true;
                      }
                      return false;
                    }""",
                    name,
                )
                if not ok:
                    return False
            else:
                # Click the list row, not only the tiny title span
                clicked = self.page.evaluate(
                    """(name) => {
                      const pane = document.querySelector('#pane-side');
                      if (!pane) return false;
                      for (const el of pane.querySelectorAll('span[title]')) {
                        if ((el.getAttribute('title') || '').trim() !== name) continue;
                        const row =
                          el.closest('[role="listitem"]') ||
                          el.closest('div[data-testid="cell-frame-container"]');
                        (row || el).dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
                        (row || el).dispatchEvent(new MouseEvent('mouseup', {bubbles:true}));
                        (row || el).click();
                        return true;
                      }
                      return false;
                    }""",
                    name,
                )
                if not clicked:
                    exact.first.click(timeout=5000)

            self.page.wait_for_timeout(1500)
            try:
                self.page.locator("#main header").first.wait_for(state="visible", timeout=8000)
            except Exception:
                pass
            active = self._active_chat_name()
            # Subtitle-only headers still mean the chat pane opened
            if active == "unknown":
                # Fallback: conversation composer visible?
                composer = self.page.locator(
                    "footer div[contenteditable='true'], #main div[contenteditable='true'][role='textbox']"
                )
                if composer.count() > 0 and composer.first.is_visible():
                    print(f"[wa] click '{name}' — chat pane open (header unknown)")
                    return True
                print(f"[wa] click '{name}' done but header still unknown")
                return False
            if active == name or name.lower() in active.lower() or active.lower() in name.lower():
                return True
            if "click here" in active.lower() or "contact info" in active.lower() or active.lower() == "business account":
                # Wrong subtitle read — pane is still likely the opened chat
                return True
            print(f"[wa] opened header={active!r} expected={name!r}")
            return True
        except Exception as exc:
            print(f"[wa] click list '{name}' failed: {exc}")
            return False

    @staticmethod
    def _is_own_list_preview(preview: str) -> bool:
        p = (preview or "").strip().lower()
        return p.startswith("you:") or p.startswith("you :") or p.startswith("you\u200e:")

    def _chats_to_check(self) -> list[str]:
        """
        Personal chats that need a reply:
        - unread badge, OR
        - chat-list preview text changed since last poll (phone often clears unread)
        Groups are skipped. Person-style names are checked first.
        """
        personal: list[str] = []
        other: list[str] = []
        seen: set[str] = set()
        unread_n = 0
        changed_n = 0
        try:
            allow_groups = config.get_allow_group_messages()
        except Exception:
            allow_groups = False

        rows = self._chat_list_rows()
        for row in rows:
            name = row.get("name") or ""
            preview = (row.get("preview") or "").strip()
            label = row.get("label") or ""
            unread = bool(row.get("unread"))
            if not name or name in self._skip_chats or _is_status_name(name):
                continue
            if self._looks_like_group_name(name, label):
                if not allow_groups:
                    self._skip_chats.add(name)
                    continue

            old = self._list_previews.get(name)
            # First time we see this name after start — remember, don't fire yet
            if old is None:
                self._list_previews[name] = preview
                continue

            changed = bool(
                preview
                and self._normalize_text(preview) != self._normalize_text(old)
            )
            self._list_previews[name] = preview

            if unread:
                unread_n += 1
            if changed:
                changed_n += 1

            # Own outbound in the list must never trigger a GPT reply
            if self._is_own_list_preview(preview):
                continue
            if preview and self._normalize_text(preview) in self._outbound_texts:
                continue

            if not unread and not changed:
                continue
            # Already answered this exact preview/text — wait for a NEW user message
            if preview and self._already_answered(name, preview):
                continue
            if name in seen:
                continue
            seen.add(name)
            if self._looks_like_personal_name(name):
                personal.append(name)
            else:
                other.append(name)

        # Stash last scan stats for heartbeat
        self._last_scan_stats = {
            "rows": len(rows),
            "unread": unread_n,
            "changed": changed_n,
            "queued": len(personal) + len(other),
        }
        return (personal + other)[:5]

    def _collect_from_open_chat(
        self, force: bool = False, expected_name: str | None = None
    ) -> IncomingMessage | None:
        # Determine whether this open chat is a group
        is_group = self._is_group_chat()
        try:
            allow_groups = config.get_allow_group_messages()
        except Exception:
            allow_groups = False

        if is_group and not allow_groups:
            name = self._active_chat_name()
            if name and name != "unknown":
                self._skip_chats.add(name)
            return None
        chat_name = self._active_chat_name()
        # Prefer the contact we intentionally opened (header DOM often shows subtitle first)
        if expected_name:
            if (
                not chat_name
                or chat_name == "unknown"
                or "click here" in chat_name.lower()
                or "contact info" in chat_name.lower()
                or chat_name.lower() == "business account"
            ):
                chat_name = expected_name
            elif expected_name.lower() not in chat_name.lower() and chat_name.lower() not in expected_name.lower():
                chat_name = expected_name
        if not chat_name or chat_name == "unknown" or _is_status_name(chat_name):
            return None
        if self._looks_like_group_name(chat_name) and not allow_groups:
            self._skip_chats.add(chat_name)
            return None
        text = self._last_incoming_text()
        if not text and expected_name:
            # Fallback: unread list preview (still enough to ask ChatGPT)
            text = self._preview_for_name(expected_name)
            if text:
                print(f"[wa] using chat-list preview for {expected_name!r}: {text[:80]!r}")
        if not text:
            print(f"[wa] open chat {chat_name!r} but no incoming text found")
            return None
        # Never re-process our own outbound WhatsApp messages
        if self._normalize_text(text) in self._outbound_texts:
            return None
        if self._already_answered(chat_name, text):
            return None
        fp = self._fingerprint(chat_name, text)
        # Never bypass handled/seen — force only retries empty-text cases above
        if fp in self._seen:
            return None
        self._seen.add(fp)
        if len(self._seen) > 2000:
            self._seen = set(list(self._seen)[-1000:])
        return IncomingMessage(chat_name=chat_name, text=text, fingerprint=fp, is_group=is_group)

    def poll_new_messages(self) -> list[IncomingMessage]:
        if not self._ready:
            return []

        found: list[IncomingMessage] = []
        self._poll_count += 1

        to_open = self._chats_to_check()
        if to_open:
            print(f"[wa] chats needing reply: {', '.join(to_open)}")

        for name in to_open:
            try:
                # Refresh preview from list before opening (fallback for AI prompt)
                self._preview_for_name(name)
                opened = False
                try:
                    opened = self._click_chat_in_list(name)
                except Exception:
                    opened = False
                if not opened:
                    opened = self.open_chat_by_name(name)
                if not opened:
                    print(f"[wa] could not open chat '{name}' — trying preview-only reply")
                    preview = self._preview_for_name(name)
                    if preview and not self._already_answered(name, preview):
                        fp = self._fingerprint(name, preview)
                        if fp not in self._seen:
                            self._seen.add(fp)
                            found.append(
                                IncomingMessage(
                                    chat_name=name, text=preview, fingerprint=fp, is_group=False
                                )
                            )
                    continue

                msg = self._collect_from_open_chat(expected_name=name)
                if not msg:
                    self.page.wait_for_timeout(900)
                    msg = self._collect_from_open_chat(force=True, expected_name=name)
                if not msg:
                    self.page.wait_for_timeout(700)
                    msg = self._collect_from_open_chat(force=True, expected_name=name)

                if msg:
                    found.append(msg)
            except Exception as exc:
                print(f"[wa] collect '{name}' error: {exc}")

        # Also process the chat currently open in WhatsApp Web (if personal + new text)
        try:
            active = self._active_chat_name()
            if self._poll_count % 8 == 0:
                st = self._last_scan_stats or {}
                print(
                    f"[wa] heartbeat open_chat={active!r} group={self._is_group_chat()} "
                    f"rows={st.get('rows', 0)} unread={st.get('unread', 0)} "
                    f"changed={st.get('changed', 0)} queued={st.get('queued', 0)}"
                )
            msg = self._collect_from_open_chat()
            if msg and all(m.fingerprint != msg.fingerprint for m in found):
                found.append(msg)
        except Exception as exc:
            print(f"[wa] poll error: {exc}")

        return found

    def send_message_to_active_chat(self, text: str) -> None:
        body = str(text or "").strip()
        if not body:
            return

        composer = self.page.locator(
            "div[contenteditable='true'][data-tab='10'], "
            "footer div[contenteditable='true'], "
            "div[contenteditable='true'][role='textbox']"
        ).first
        composer.click(timeout=8000)
        composer.fill("")
        try:
            composer.fill(body)
        except Exception:
            composer.type(body, delay=15)

        send_btn = self.page.locator(
            "button[aria-label='Send'], button[data-testid='send'], span[data-icon='send']"
        )
        if send_btn.count() > 0 and send_btn.first.is_visible():
            send_btn.first.click()
        else:
            composer.press("Enter")

        self.page.wait_for_timeout(500)
        chat_name = self._active_chat_name()
        if _is_status_name(chat_name):
            chat_name = "me"
        self._seen.add(self._fingerprint(chat_name, body))
        self.remember_outbound(body)

    def open_chat_by_name(self, name: str) -> bool:
        if self._click_chat_in_list(name):
            return True
        try:
            search = self.page.locator(
                "div[contenteditable='true'][data-tab='3'], "
                "[data-testid='chat-list-search'] div[contenteditable='true'], "
                "div[title='Search input textbox']"
            ).first
            search.click(timeout=5000)
            search.fill("")
            search.fill(name)
            self.page.wait_for_timeout(900)
            result = self.page.locator(f"#pane-side span[title='{name}']").first
            if result.count() == 0:
                return False
            result.click(timeout=5000)
            self.page.wait_for_timeout(600)
            return True
        except Exception as exc:
            print(f"[wa] open_chat_by_name failed: {exc}")
            return False

    def open_chat_by_phone(self, phone: str | None = None) -> bool:
        """Open a chat by phone digits (e.g. 918467914076) via WhatsApp Web send URL."""
        digits = re.sub(r"\D", "", phone or self.phone)
        if not digits:
            return False
        try:
            self.page.goto(
                f"{WHATSAPP_URL}send?phone={digits}",
                wait_until="domcontentloaded",
            )
            self.page.wait_for_timeout(2000)
            # Dismiss "Continue to chat" if shown
            cont = self.page.locator(
                "a[href*='send'], button:has-text('Continue'), div[role='button']:has-text('Continue')"
            )
            if cont.count() > 0 and cont.first.is_visible():
                cont.first.click()
                self.page.wait_for_timeout(1500)
            return True
        except Exception as exc:
            print(f"[wa] open_chat_by_phone failed: {exc}")
            return False

    def reply(self, message: IncomingMessage, reply_text: str) -> None:
        current = self._active_chat_name()
        if _is_status_name(current) or (
            current != message.chat_name
            and message.chat_name.lower() not in current.lower()
            and current.lower() not in message.chat_name.lower()
        ):
            if not self._click_chat_in_list(message.chat_name) and not self.open_chat_by_name(
                message.chat_name
            ):
                raise RuntimeError(f"Could not open chat: {message.chat_name}")
        self.send_message_to_active_chat(reply_text)
        self.remember_outbound(reply_text)

    def run_forever(self, on_message: Callable[[IncomingMessage], None]) -> None:
        from playwright.sync_api import Error as PlaywrightError

        print("[wa] Polling for messages (Ctrl+C to stop)...")
        while True:
            try:
                if self._page is None or self._page.is_closed():
                    raise RuntimeError(
                        "WhatsApp browser window was closed. Restart bot.py and keep both windows open."
                    )
                messages = self.poll_new_messages()
                for msg in messages:
                    tag = "[group] " if getattr(msg, "is_group", False) else ""
                    print(f"[wa] {tag}<- {msg.chat_name}: {msg.text[:120]}")
                    # Simple group notification detection
                    try:
                        if getattr(msg, "is_group", False) and re.search(r"\b(left|removed|added|joined)\b", msg.text, re.I):
                            print(f"[wa] [group-notify] {msg.chat_name}: {msg.text[:160]}")
                    except Exception:
                        pass
                    on_message(msg)
                self.page.wait_for_timeout(int(self.poll_interval_sec * 1000))
            except PlaywrightError as exc:
                if "closed" in str(exc).lower():
                    raise RuntimeError(
                        "WhatsApp browser was closed during polling. "
                        "Keep WhatsApp and ChatGPT windows open, then run: python bot.py"
                    ) from exc
                raise

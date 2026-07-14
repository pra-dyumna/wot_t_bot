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


@dataclass
class IncomingMessage:
    chat_name: str
    text: str
    fingerprint: str


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
        self._ready = False
        self._poll_count = 0

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
        print("[wa] Policy: personal unread DMs only (groups ignored). Keep browsers open.")

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
        raw = f"{chat_name}||{text}".strip()
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

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
                  const titled = header.querySelector('span[title]');
                  if (titled) {
                    const t = (titled.getAttribute('title') || titled.textContent || '').trim();
                    if (t) return t;
                  }
                  const auto = header.querySelector('span[dir="auto"]');
                  if (auto) return (auto.textContent || '').trim();
                  return '';
                }"""
            )
            if name and str(name).strip():
                return str(name).strip()
        except Exception:
            pass
        try:
            header = self.page.locator(
                "#main header span[title], #main header span[dir='auto']"
            ).first
            if header.count() > 0:
                title = header.get_attribute("title") or header.inner_text()
                return (title or "").strip()
        except Exception:
            pass
        return "unknown"

    def _last_incoming_text(self) -> str | None:
        """Best-effort read of last incoming bubble text in the open chat."""
        try:
            text = self.page.evaluate(
                """() => {
                  const nodes = [
                    ...document.querySelectorAll('div.message-in span.selectable-text'),
                    ...document.querySelectorAll('div.message-in div.copyable-text'),
                    ...document.querySelectorAll('div.message-in span[data-testid="conversation-text"]'),
                  ];
                  for (let i = nodes.length - 1; i >= 0; i--) {
                    const t = (nodes[i].innerText || nodes[i].textContent || '').trim();
                    if (t) return t;
                  }
                  return null;
                }"""
            )
            if text:
                return str(text).strip()
        except Exception:
            pass
        selectors = [
            "div.message-in span.selectable-text",
            "div.message-in div.copyable-text",
            "div.message-in span[data-testid='conversation-text']",
        ]
        for sel in selectors:
            nodes = self.page.locator(sel)
            n = nodes.count()
            if n > 0:
                t = nodes.nth(n - 1).inner_text().strip()
                if t:
                    return t
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

                  // WhatsApp Web DOM changes often — try several row shapes
                  let rowNodes = [...pane.querySelectorAll('[role="listitem"]')];
                  if (rowNodes.length === 0) {
                    rowNodes = [...pane.querySelectorAll('div[data-testid="cell-frame-container"]')];
                  }
                  if (rowNodes.length === 0) {
                    // Fallback: any element in pane that has a titled contact span
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
                      const lines = (row.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
                      const preview = lines.length >= 2 ? lines[1] : '';
                      const unread =
                        /\\bunread\\b/i.test(label) ||
                        !!row.querySelector('[data-testid="icon-unread-count"]') ||
                        !!row.querySelector('span[aria-label*="unread" i]');
                      seen.add(name);
                      out.push({ name, preview, unread: !!unread, label });
                    }
                    return out;
                  }

                  for (const row of rowNodes) {
                    const label = (row.getAttribute('aria-label') || '') + ' ' + (row.innerText || '');
                    if (/\\d+\\s+(participants|members)/i.test(label)) continue;
                    const titleEl = row.querySelector('span[title]');
                    const name = (titleEl && (titleEl.getAttribute('title') || titleEl.textContent) || '').trim();
                    if (!name || seen.has(name)) continue;
                    const lines = (row.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
                    const preview = lines.length >= 2 ? lines[1] : '';
                    const unread =
                      /\\bunread\\b/i.test(label) ||
                      !!row.querySelector('[data-testid="icon-unread-count"]') ||
                      !!row.querySelector('span[aria-label*="unread" i]');
                    seen.add(name);
                    out.push({ name, preview, unread: !!unread, label });
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
        return False

    def _click_chat_in_list(self, name: str) -> bool:
        """Open a chat by clicking its row in the left list (no search box)."""
        try:
            ok = self.page.evaluate(
                """(name) => {
                  const pane = document.querySelector('#pane-side');
                  if (!pane) return false;
                  const titles = pane.querySelectorAll('span[title]');
                  for (const el of titles) {
                    const t = (el.getAttribute('title') || el.textContent || '').trim();
                    if (t !== name) continue;
                    const row =
                      el.closest('[role="listitem"]') ||
                      el.closest('div[data-testid="cell-frame-container"]') ||
                      el.parentElement;
                    if (row) { row.click(); return true; }
                    el.click();
                    return true;
                  }
                  return false;
                }""",
                name,
            )
            if ok:
                self.page.wait_for_timeout(1000)
            return bool(ok)
        except Exception as exc:
            print(f"[wa] click list '{name}' failed: {exc}")
            return False

    def _chats_to_check(self) -> list[str]:
        """
        Personal chats with an UNSEEN/unread badge only.
        Groups are skipped. No contact-name special cases.
        """
        names: list[str] = []
        seen: set[str] = set()
        for row in self._chat_list_rows():
            name = row.get("name") or ""
            preview = (row.get("preview") or "").strip()
            label = row.get("label") or ""
            unread = bool(row.get("unread"))
            if not name or name in self._skip_chats:
                continue
            if self._looks_like_group_name(name, label):
                self._skip_chats.add(name)
                continue
            # Always keep preview map updated so later unread detection stays stable
            self._list_previews[name] = preview
            if not unread:
                continue
            if name in seen:
                continue
            seen.add(name)
            names.append(name)
            if len(names) >= 5:
                break
        return names

    def _collect_from_open_chat(self, force: bool = False) -> IncomingMessage | None:
        if self._is_group_chat():
            name = self._active_chat_name()
            if name and name != "unknown":
                self._skip_chats.add(name)
            return None
        chat_name = self._active_chat_name()
        if not chat_name or chat_name == "unknown":
            return None
        if self._looks_like_group_name(chat_name):
            self._skip_chats.add(chat_name)
            return None
        text = self._last_incoming_text()
        if not text:
            if self._poll_count % 8 == 0:
                print(f"[wa] open chat {chat_name!r} but no incoming text found")
            return None
        fp = self._fingerprint(chat_name, text)
        if not force and fp in self._seen:
            return None
        self._seen.add(fp)
        if len(self._seen) > 2000:
            self._seen = set(list(self._seen)[-1000:])
        return IncomingMessage(chat_name=chat_name, text=text, fingerprint=fp)

    def poll_new_messages(self) -> list[IncomingMessage]:
        if not self._ready:
            return []

        found: list[IncomingMessage] = []
        self._poll_count += 1

        to_open = self._chats_to_check()
        if to_open:
            print(f"[wa] unread personal chats: {', '.join(to_open)}")

        for name in to_open:
            try:
                if not self._click_chat_in_list(name):
                    self.open_chat_by_name(name)
                msg = self._collect_from_open_chat()
                if msg:
                    found.append(msg)
            except Exception as exc:
                print(f"[wa] collect '{name}' error: {exc}")

        # Also process the chat currently open in WhatsApp Web (if personal + new text)
        try:
            active = self._active_chat_name()
            if self._poll_count % 8 == 0:
                print(f"[wa] heartbeat open_chat={active!r} group={self._is_group_chat()}")
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
        self._seen.add(self._fingerprint(chat_name, body))

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
        if current != message.chat_name:
            if not self._click_chat_in_list(message.chat_name) and not self.open_chat_by_name(
                message.chat_name
            ):
                raise RuntimeError(f"Could not open chat: {message.chat_name}")
        self.send_message_to_active_chat(reply_text)

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
                    print(f"[wa] <- {msg.chat_name}: {msg.text[:120]}")
                    on_message(msg)
                self.page.wait_for_timeout(int(self.poll_interval_sec * 1000))
            except PlaywrightError as exc:
                if "closed" in str(exc).lower():
                    raise RuntimeError(
                        "WhatsApp browser was closed during polling. "
                        "Keep WhatsApp and ChatGPT windows open, then run: python bot.py"
                    ) from exc
                raise

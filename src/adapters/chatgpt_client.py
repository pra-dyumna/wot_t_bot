"""
Local ChatGPT client via Playwright (no OpenAI API key).

Session saved under config/gpt-auth/ (mirrors wat-a-bot auth folders).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page, sync_playwright, TimeoutError as PlaywrightTimeout

from src.utils.config import config

CHATGPT_URL = "https://chatgpt.com/"


def _qa_path() -> Path:
    return config.get_qa_results_path()


def _maybe_migrate_legacy_gpt_session(auth_dir: Path) -> None:
    legacy = config.root / ".browser_data"
    if auth_dir.exists() and any(auth_dir.iterdir()):
        return
    if not legacy.exists() or not any(legacy.iterdir()):
        return
    auth_dir.parent.mkdir(parents=True, exist_ok=True)
    if auth_dir.exists():
        return
    print(f"[gpt] Migrating legacy session {legacy} -> {auth_dir}")
    shutil.copytree(legacy, auth_dir)


def save_qa(question: str, answer: str, path: Path | None = None) -> Path:
    out = path or _qa_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "question": question,
        "answer": answer,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    records: list[dict] = []
    if out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                records = existing
            elif isinstance(existing, dict):
                records = [existing]
        except json.JSONDecodeError:
            records = []
    records.append(record)
    out.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def wait_for_login(page: Page, timeout_sec: int = 300) -> None:
    print("[gpt] If needed, log in to ChatGPT in the browser window...")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        composer = page.locator(
            "#prompt-textarea, [data-testid='composer-text-input'], div[contenteditable='true']"
        )
        if composer.count() > 0 and composer.first.is_visible():
            print("[gpt] Logged in / chat ready.")
            return
        page.wait_for_timeout(1000)
    raise TimeoutError("Timed out waiting for ChatGPT login / composer.")


def send_query(page: Page, query: str, response_timeout_ms: int = 120_000) -> str:
    composer = page.locator("#prompt-textarea").first
    if composer.count() == 0:
        composer = page.locator("div[contenteditable='true']").first

    composer.click()
    composer.fill("")
    composer.fill(query)

    send = page.locator('[data-testid="send-button"], button[aria-label*="Send"]')
    if send.count() > 0 and send.first.is_enabled():
        send.first.click()
    else:
        composer.press("Enter")

    stop = page.locator('[data-testid="stop-button"], button[aria-label*="Stop"]')
    try:
        stop.first.wait_for(state="visible", timeout=15_000)
        stop.first.wait_for(state="hidden", timeout=response_timeout_ms)
    except PlaywrightTimeout:
        page.wait_for_timeout(2000)

    page.wait_for_timeout(1500)

    messages = page.locator('[data-message-author-role="assistant"]')
    if messages.count() == 0:
        messages = page.locator(".markdown, .prose")

    if messages.count() == 0:
        raise RuntimeError("No assistant response found on the page.")

    return messages.last.inner_text().strip()


class ChatGPTClient:
    """Long-lived Playwright session so each WhatsApp message reuses the same browser."""

    def __init__(
        self,
        headless: bool | None = None,
        user_data_dir: Path | None = None,
        playwright=None,
    ) -> None:
        self.headless = config.get_gpt_headless() if headless is None else headless
        self.user_data_dir = user_data_dir or config.get_gpt_auth_dir()
        self._external_pw = playwright is not None
        self._playwright = playwright
        self._context = None
        self._page: Page | None = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("ChatGPTClient is not started. Call start() first.")
        return self._page

    def start(self) -> None:
        if self._page is not None:
            return

        _maybe_migrate_legacy_gpt_session(self.user_data_dir)
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        print(f"[gpt] Starting browser (data={self.user_data_dir})...")
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.goto(CHATGPT_URL, wait_until="domcontentloaded")
        wait_for_login(self._page)
        print("[gpt] Ready.")

    def ask(self, query: str, save: bool = True) -> str:
        text = str(query or "").strip()
        if not text:
            raise ValueError("Empty query.")

        try:
            new_chat = self.page.locator(
                'a[href="/"], button:has-text("New chat"), [data-testid="create-new-chat-button"]'
            )
            if new_chat.count() > 0 and new_chat.first.is_visible():
                new_chat.first.click()
                self.page.wait_for_timeout(800)
        except Exception:
            pass

        composer = self.page.locator("#prompt-textarea, div[contenteditable='true']")
        if composer.count() == 0 or not composer.first.is_visible():
            self.page.goto(CHATGPT_URL, wait_until="domcontentloaded")
            wait_for_login(self.page)

        print(f"[gpt] Asking ({len(text)} chars)...")
        reply = send_query(self.page, text)
        if save:
            save_qa(text, reply)
        print(f"[gpt] Got reply ({len(reply)} chars).")
        return reply

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            self._context = None
            self._page = None
            if self._playwright is not None and not self._external_pw:
                self._playwright.stop()
                self._playwright = None
            print("[gpt] Closed.")


def ask_chatgpt(query: str, headless: bool = False) -> str:
    client = ChatGPTClient(headless=headless)
    try:
        client.start()
        return client.ask(query)
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Query ChatGPT via Playwright")
    parser.add_argument("query", nargs="?", help="Question to send to ChatGPT")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    query = args.query or input("Enter your query: ").strip()
    if not query:
        print("Empty query.", file=sys.stderr)
        return 1

    print(f"\n>>> Sending: {query}\n")
    try:
        reply = ask_chatgpt(query, headless=args.headless)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print("--- ChatGPT response ---")
    print(reply)
    print("------------------------")
    print(f"Saved to: {_qa_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

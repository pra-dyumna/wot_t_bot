"""
wot_t_bot — WhatsApp (Playwright) + ChatGPT (Playwright).

Same idea as wat-a-bot (Node whatsapp-web.js + LocalAuth), but:
- WhatsApp Web via Playwright in Python
- AI replies via chatgpt.com Playwright session (no OpenAI API / no Ollama)

Run from project root:
  python bot.py
"""

from __future__ import annotations

import re
import signal
import sys
from pathlib import Path

# Ensure project root is on sys.path when run as `python bot.py`
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from src.adapters.chatgpt_client import ChatGPTClient
from src.adapters.whatsapp_client import IncomingMessage, WhatsAppClient
from src.utils.config import config


def build_prompt(message: IncomingMessage) -> str:
    return (
        "You are me, chatting on WhatsApp with this contact.\n"
        "Write ONE natural WhatsApp reply to their latest message.\n\n"
        "Strict rules:\n"
        "- Output ONLY the reply text I should send.\n"
        "- Do NOT say you are an AI/bot/assistant.\n"
        "- Do NOT write prefaces like \"Here's a reply\", \"You can send\", \"Edit\", options, or bullet lists.\n"
        "- Do NOT use markdown.\n"
        "- Keep it short (1–3 sentences) unless they ask for detail.\n"
        "- Match their language (English / Hindi / mix) when obvious.\n\n"
        f"Contact name: {message.chat_name}\n"
        f"Their message:\n{message.text}\n\n"
        "WhatsApp reply:"
    )


def clean_reply(raw: str) -> str:
    """Strip ChatGPT UI / meta wrappers so only sendable chat text remains."""
    text = str(raw or "").strip()
    if not text:
        return text

    # Drop common helper phrases ChatGPT adds
    patterns = [
        r"(?is)^\s*here(?:'s| is)\s+(?:a\s+)?(?:friendly\s+)?reply[^:\n]*[:\-–]\s*",
        r"(?is)^\s*you can send[:\-–]\s*",
        r"(?is)^\s*suggested reply[:\-–]\s*",
        r"(?is)^\s*response[:\-–]\s*",
        r"(?is)^\s*edit\s*",
        r"(?is)^\s*option\s*\d+[:\-–]\s*",
    ]
    for pat in patterns:
        text = re.sub(pat, "", text).strip()

    # If model gave quoted block, unwrap once
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1].strip()

    # Remove lines that are clearly UI chrome
    lines = []
    for line in text.splitlines():
        low = line.strip().lower()
        if low in {"edit", "copy", "share", "regenerate", "继续", "continue"}:
            continue
        if low.startswith("here") and "reply" in low and len(low) < 60:
            continue
        lines.append(line)
    text = "\n".join(lines).strip()

    # Soft length guard for WhatsApp
    if len(text) > 1200:
        text = text[:1200].rstrip() + "…"
    return text


def main() -> int:
    config.log_startup()

    auto_reply = config.get_auto_reply()
    wa_headless = config.get_wa_headless()
    gpt_headless = config.get_gpt_headless()
    poll_sec = config.get_poll_interval_sec()
    phone = config.get_wa_phone()
    client_id = config.get_wa_client_id()

    print("wot_t_bot - WhatsApp + ChatGPT (Playwright)\n")

    pw = sync_playwright().start()
    gpt = ChatGPTClient(headless=gpt_headless, playwright=pw)
    wa = WhatsAppClient(
        headless=wa_headless,
        poll_interval_sec=poll_sec,
        playwright=pw,
        phone=phone or client_id,
    )

    def shutdown(*_args) -> None:
        print("\nShutting down...")
        try:
            wa.close()
        except Exception:
            pass
        try:
            gpt.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    gpt.start()
    wa.start()

    def handle(message: IncomingMessage) -> None:
        print(f"[bot] Query from {message.chat_name}: {message.text[:120]!r}")
        try:
            answer = clean_reply(gpt.ask(build_prompt(message), save=True))
        except Exception as exc:
            print(f"[bot] ChatGPT failed: {exc}")
            if auto_reply:
                try:
                    wa.reply(
                        message,
                        "Sorry, I could not get an answer right now. Please try again.",
                    )
                    wa.mark_handled(message)
                except Exception as send_exc:
                    print(f"[bot] Failed to send error reply: {send_exc}")
            return

        if not answer:
            print("[bot] Empty GPT reply after cleanup — skipping send.")
            wa.mark_handled(message)
            return

        print(f"[bot] Answer preview: {answer[:160].replace(chr(10), ' ')}")
        if not auto_reply:
            print("[bot] AUTO_REPLY=false - not sending WhatsApp reply.")
            wa.mark_handled(message)
            return

        try:
            wa.reply(message, answer)
            wa.mark_handled(message)
            print(f"[bot] -> replied once to {message.chat_name} (waiting for their next message)")
        except Exception as exc:
            print(f"[bot] WhatsApp send failed: {exc}")

    label = phone or client_id
    print(f"[bot] Listening (session={label}). Unread personal DM -> one GPT reply -> wait for next msg.\n")
    try:
        wa.run_forever(handle)
    except KeyboardInterrupt:
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

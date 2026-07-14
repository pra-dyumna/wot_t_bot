"""
wot_t_bot — WhatsApp (Playwright) + ChatGPT (Playwright).

Same idea as wat-a-bot (Node whatsapp-web.js + LocalAuth), but:
- WhatsApp Web via Playwright in Python
- AI replies via chatgpt.com Playwright session (no OpenAI API / no Ollama)

Run from project root:
  python bot.py
"""

from __future__ import annotations

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
        "You are helping reply on WhatsApp. "
        "Answer the user's message clearly and helpfully. "
        "Keep it concise (a few short sentences) unless they ask for detail. "
        "Do not mention that you are an AI unless they ask.\n\n"
        f"WhatsApp message from {message.chat_name}:\n"
        f"{message.text}"
    )


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
        print(f"[bot] Query from {message.chat_name}")
        try:
            answer = gpt.ask(build_prompt(message), save=True)
        except Exception as exc:
            print(f"[bot] ChatGPT failed: {exc}")
            if auto_reply:
                try:
                    wa.reply(
                        message,
                        "Sorry, I could not get an answer right now. Please try again.",
                    )
                except Exception as send_exc:
                    print(f"[bot] Failed to send error reply: {send_exc}")
            return

        print(f"[bot] Answer preview: {answer[:160].replace(chr(10), ' ')}")
        if not auto_reply:
            print("[bot] AUTO_REPLY=false - not sending WhatsApp reply.")
            return

        try:
            wa.reply(message, answer)
            print(f"[bot] -> replied to {message.chat_name}")
        except Exception as exc:
            print(f"[bot] WhatsApp send failed: {exc}")

    label = phone or client_id
    print(f"[bot] Listening (session={label}). Scan QR with that WhatsApp phone. Send a personal DM.\n")
    try:
        wa.run_forever(handle)
    except KeyboardInterrupt:
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

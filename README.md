# wot_t_bot

Personal WhatsApp assistant built with **Python + Playwright**.

| Piece | How it works |
|-------|----------------|
| WhatsApp | WhatsApp Web in Chromium (QR login, session saved locally) |
| AI reply | ChatGPT website in a second browser (no OpenAI API key) |
| Policy | **Unread personal DMs only** → ask ChatGPT → auto-reply |

Same product idea as `wat-a-bot`, but Python + ChatGPT instead of Node + Ollama.

---

## How replies work (important)

```
Anyone sends a personal message to YOUR WhatsApp number
        ↓
Message stays UNSEEN / unread in WhatsApp Web chat list
        ↓
Bot opens that chat, reads the text
        ↓
Bot sends the text to ChatGPT (browser)
        ↓
Bot types ChatGPT's answer back in that chat
```

**Rules**

- Works for **any contact** (no special names).
- **Groups are ignored.**
- Best results when the chat still shows an **unread** badge.  
  If you open the chat on your phone first, WhatsApp may clear “unseen” and the bot can miss it.
- Keep **both** browser windows open while the bot runs.

Terminal success looks like:

```text
[wa] unread personal chats: Some Contact
[wa] <- Some Contact: hello
[gpt] Asking...
[bot] -> replied to Some Contact
```

If you never see `[wa] <- ...`, ChatGPT was never called.

---

## Project layout

```text
wot_t_bot/
├── bot.py                      # Entry: npm-style "python bot.py"
├── .env                        # Your config (gitignored)
├── .env.example                # Template — copy to .env
├── .gitignore
├── requirements.txt
├── README.md
├── DESIGN.md                   # Future: profiles, SQLite, RAG
├── src/
│   ├── adapters/
│   │   ├── whatsapp_client.py  # Unread DMs + send reply
│   │   └── chatgpt_client.py   # Ask ChatGPT via Playwright
│   ├── utils/config.py         # Loads .env
│   └── services/               # Reserved for next features
├── config/
│   ├── wa-auth/                # WhatsApp session (gitignored)
│   └── gpt-auth/               # ChatGPT session (gitignored)
├── data/                       # Local logs / future DB
└── scripts/ask_chatgpt.py
```

---

## Setup

```powershell
cd D:\AI_project\wbot\wot_t_bot
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env
```

Edit `.env`:

```env
WA_PHONE=917905975835
WA_HEADLESS=false
GPT_HEADLESS=false
AUTO_REPLY=true
POLL_INTERVAL_SEC=2.5
```

`WA_PHONE` is only a **session folder label**. The real account is whichever phone scans the QR.

---

## Run

```powershell
.\venv\Scripts\python.exe -u bot.py
```

1. Log in to ChatGPT in the first window (once).  
2. Scan WhatsApp QR with your bot phone.  
3. From another phone, send an unread personal DM to that number.  
4. Bot should reply automatically when `AUTO_REPLY=true`.

Stop with `Ctrl+C`.

---

## Config files

| File | Role |
|------|------|
| `.env` | Real settings on your machine (not committed) |
| `.env.example` | Safe copy-paste template |
| `.gitignore` | Blocks secrets, sessions, `venv/`, logs from git |

---

## vs wat-a-bot

| | wat-a-bot | wot_t_bot |
|--|-----------|-----------|
| Language | Node.js | Python |
| WhatsApp | whatsapp-web.js | Playwright WhatsApp Web |
| AI | Ollama (optional) | ChatGPT browser |
| Send | Manual `/send` drafts | Auto-reply to unread DMs |

---

## Next (see DESIGN.md)

Contact profiles, chat memory (SQLite), optional FAISS RAG, better tone per person.

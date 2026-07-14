# wot_t_bot

Personal WhatsApp assistant: **Python + Playwright**.

| Piece | How it works |
|-------|----------------|
| WhatsApp | WhatsApp Web in Chromium (QR login, session saved locally) |
| AI reply | ChatGPT website in a second browser (**no** OpenAI API key) |
| Policy | Personal DMs → ChatGPT → one auto-reply per message |

Same idea as `wat-a-bot`, but Python + ChatGPT browser instead of Node + Ollama.

---

## How replies work

```
Someone sends a personal DM to YOUR WhatsApp number
        ↓
Bot notices unread badge AND/OR chat-list preview text change
        ↓
Opens that chat, reads the message (or uses list preview)
        ↓
Asks ChatGPT in the other browser
        ↓
Sends ONE cleaned reply back → waits for the next NEW message
```

**Rules**

- Any personal contact (no hardcoded names).
- **Groups ignored** by default (`ALLOW_GROUP_MESSAGES=false`).
- **One reply per inbound text** — same message is not answered twice.
- Bot ignores its **own** outbound text (no self-reply loop).
- Keep **both** browser windows open while running.
- Prefer testing from **another phone**. Messaging yourself on the same account is unreliable.

**Important:** Send a **new** DM **after** the bot has started.  
If you open the chat on your phone first, WhatsApp may clear unread; the bot still tries via **preview change**, but a fresh message after startup is most reliable.

### Terminal: what success looks like

```text
[wa] chats needing reply: Some Contact
[wa] <- Some Contact: hello
[gpt] Asking...
[bot] -> replied to Some Contact
[wa] marked handled: 'Some Contact'
```

Idle heartbeat (normal while waiting):

```text
[wa] heartbeat open_chat='unknown' group=False rows=61 unread=0 changed=0 queued=0
```

- `open_chat='unknown'` → no chat open in the pane (OK when idle).
- `queued=0` → nothing to answer yet.
- If you never see `chats needing reply` / `[wa] <-`, ChatGPT was never called.

---

## Project layout

```text
wot_t_bot/
├── bot.py                         # Entry point — run this
├── .env                           # Your secrets/settings (gitignored)
├── .env.example                   # Template — copy to .env
├── .gitignore
├── requirements.txt
├── README.md
├── DESIGN.md                      # Future: profiles, SQLite, RAG
├── src/
│   ├── adapters/
│   │   ├── whatsapp_client.py     # Detect DMs, open chat, send reply
│   │   └── chatgpt_client.py      # Ask ChatGPT via Playwright
│   ├── utils/
│   │   └── config.py              # Loads .env
│   └── services/                  # Reserved for later features
├── config/
│   ├── wa-auth/session-<phone>/   # WhatsApp Chromium profile (gitignored)
│   └── gpt-auth/                  # ChatGPT Chromium profile (gitignored)
├── data/                          # Local Q&A logs / future DB
└── scripts/
    └── ask_chatgpt.py             # Optional: ChatGPT-only smoke test
```

---

## Setup (first time)

```powershell
cd D:\AI_project\wbot\wot_t_bot
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env
```

Edit `.env` (see [What to change](#what-to-change-when-you-need-to-update-things)).

---

## Run

Always from the project root:

```powershell
cd D:\AI_project\wbot\wot_t_bot
.\venv\Scripts\python.exe -u bot.py
```

(`-u` = unbuffered logs so prints show immediately.)

1. **ChatGPT window** — log in once if asked; session is reused under `config/gpt-auth/`.
2. **WhatsApp window** — scan QR once with the bot phone if needed; session under `config/wa-auth/session-<WA_PHONE>/`.
3. From **another** phone, send a personal DM to that number.
4. With `AUTO_REPLY=true`, the bot replies automatically.

Stop: `Ctrl+C`.

### ChatGPT only (optional)

```powershell
.\venv\Scripts\python.exe -u scripts\ask_chatgpt.py
```

---

## What to change when you need to update things

| Goal | Edit this |
|------|-----------|
| Phone / session label, headless, auto-reply, poll speed | **`.env`** (copy from `.env.example` if missing) |
| New env knobs / defaults | **`src/utils/config.py`** + **`.env.example`** |
| How WhatsApp finds messages / sends | **`src/adapters/whatsapp_client.py`** |
| How ChatGPT is asked / scraped | **`src/adapters/chatgpt_client.py`** |
| Prompt text, reply cleanup, start/stop loop | **`bot.py`** |
| Python packages | **`requirements.txt`** then `pip install -r requirements.txt` |
| What git ignores (sessions, `.env`, `venv`) | **`.gitignore`** |
| Future DB / RAG design notes | **`DESIGN.md`** |

### `.env` keys

| Key | Meaning |
|-----|---------|
| `WA_PHONE` | Session folder label (e.g. `917905975835`). **Not** an API login — QR decides the real account. |
| `WA_CLIENT_ID` | Optional override for session folder name (defaults to `WA_PHONE`). |
| `WA_HEADLESS` / `GPT_HEADLESS` | `false` = show browsers (needed for first QR / ChatGPT login). |
| `AUTO_REPLY` | `true` = send ChatGPT answer on WhatsApp. |
| `POLL_INTERVAL_SEC` | How often to scan the chat list (default `2.5`). |
| `ALLOW_GROUP_MESSAGES` | `true` to allow group chats (default `false`). |

Example:

```env
WA_PHONE=917905975835
WA_HEADLESS=false
GPT_HEADLESS=false
AUTO_REPLY=true
POLL_INTERVAL_SEC=2.5
ALLOW_GROUP_MESSAGES=false
```

After changing `.env` or Python code, **restart** the bot (`Ctrl+C`, then run again).

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `TargetClosed` / SingletonLock / profile busy | Stop all `python.exe` bots, delete `config/**/Singleton*` if present, start **one** instance. |
| Heartbeat only, never replies | Send a **new** DM after bot start; don’t open the chat on phone first; watch for `changed` / `unread` / `queued` in heartbeat. |
| Playwright / Chromium missing | `.\venv\Scripts\python.exe -m playwright install chromium` |
| Windows print / encoding errors | Run with `-u` and `PYTHONIOENCODING=utf-8` if needed. |
| Bot answers the same message forever | Should be fixed via `mark_handled` / outbound memory; restart after pulling latest code. |
| Replies to its own messages | Status names like `online` and outbound text are filtered; restart on latest code. |

Clean restart (PowerShell):

```powershell
cd D:\AI_project\wbot\wot_t_bot
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep 3
Get-ChildItem -Recurse -Filter "Singleton*" config -ErrorAction SilentlyContinue |
  Remove-Item -Force -ErrorAction SilentlyContinue
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUNBUFFERED="1"
.\venv\Scripts\python.exe -u bot.py
```

---

## vs wat-a-bot

| | wat-a-bot | wot_t_bot |
|--|-----------|-----------|
| Language | Node.js | Python |
| WhatsApp | whatsapp-web.js | Playwright WhatsApp Web |
| AI | Ollama (optional) | ChatGPT browser |
| Send | Manual `/send` drafts | Auto-reply to personal DMs |

---

## Next (see DESIGN.md)

Contact profiles, chat memory (SQLite), optional FAISS RAG, tone per person.

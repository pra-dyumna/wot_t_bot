# wot_t_bot — Goal, How It Works, Reply Rules

Lightweight personal WhatsApp assistant (Python + Playwright).
No OpenAI API key — ChatGPT via browser. Memory via **SQLite + FAISS + small embeddings**.

---

## 1. Goal

Build a **personal WhatsApp assistant** that:

1. Connects to **your** WhatsApp account (WhatsApp Web + Playwright).
2. For each contact, **knows who they are** (number, name, relationship, how you talk).
3. **Remembers chats** (store every message).
4. When someone messages you, the bot:
   - Loads that contact’s profile + recent chat
   - Optionally searches older chats with **RAG (FAISS)**
   - Asks **ChatGPT** (Playwright) for a reply
   - Sends the reply on WhatsApp (auto-reply mode)

**Success looks like:** replies feel like *you* talking to *that person* — not a generic chatbot.

---

## 2. What the bot will reply (behavior)

| Situation | What the bot does |
|-----------|-------------------|
| Personal DM with text | Answer using ChatGPT, with that contact’s context |
| Group message | **Ignore** (no reply) |
| Media / no text | **Ignore** |
| Unknown new contact | Create a basic contact row; reply with generic helpful tone until you set profile |
| Error from ChatGPT | Short apology: ask to try again |

### Reply style rules (always in the GPT prompt)

- Short WhatsApp-style reply (1–3 short sentences unless they ask for detail).
- Match contact **language** (English / Hindi / mix) when set.
- Match **relationship tone** (casual friend vs formal work).
- Respect **topics OK / avoid** from the profile.
- Do **not** invent facts, promises, or personal details not in profile/history.
- Do **not** mention “AI / bot / automation” unless the user asks.
- Use **recent chat** so the reply continues the conversation naturally.
- Use **RAG hits** only when older context is useful (e.g. “remember what we said about X”).

### Example

**Contact:** Rahul — friend — casual — Hindi OK  
**Incoming:** `kal cricket kab?`  
**Bot reply (example):** `Shaam 5 baje ground pe milte hain. Tu free hai?`

**Contact:** Client — work — formal — English  
**Incoming:** `Can we move the call to tomorrow?`  
**Bot reply (example):** `Sure — tomorrow works. What time suits you?`

---

## 3. How it works (end-to-end)

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│ WhatsApp Web│────▶│  Bot core    │────▶│ ChatGPT browser │
│ (Playwright)│◀────│  (Python)    │◀────│ (Playwright)    │
└─────────────┘     └──────┬───────┘     └─────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │ Memory (lightweight)   │
              │  • SQLite = contacts,  │
              │    profiles, messages  │
              │  • FAISS = vector      │
              │    search over chats   │
              │  • small embedding     │
              │    model (local)       │
              └────────────────────────┘
```

### Step-by-step on each new DM

1. **Detect** new personal message on WhatsApp.
2. **Identify** contact (phone / chat name → `contacts` row).
3. **Save** incoming message to SQLite.
4. **Load profile** (relationship, tone, language, OK/avoid topics).
5. **Load recent messages** (e.g. last 15–20) from SQLite.
6. **Optional RAG:** embed the new message → search FAISS → top-k older snippets for this contact.
7. **Build prompt** = system rules + profile + recent chat + RAG snippets + incoming text.
8. **Ask ChatGPT** via Playwright → get answer text.
9. **Send** answer on WhatsApp.
10. **Save** outbound message to SQLite (+ embed into FAISS for future search).

---

## 4. What we store

### SQLite (source of truth)

**`contacts`**

| Field | Meaning |
|-------|---------|
| `chat_id` / phone | Unique WhatsApp identity |
| `display_name` | Name shown in chat |
| `relationship_type` | friend / family / work / client / other |
| `preferred_language` | english / hindi / both |
| `tone_notes` | How to talk (casual, formal, short…) |
| `topics_ok` | OK content types |
| `topics_avoid` | Things not to say |
| `notes` | Free-form owner notes |

**`messages`**

| Field | Meaning |
|-------|---------|
| `contact_id` | Who |
| `direction` | `in` or `out` |
| `text` | Message body |
| `timestamp` | When |
| `fingerprint` | Dedup key |

**`contact_facts`** (optional key-value)

Examples: `company`, `birthday`, `project_name`, `preferred_call_time`.

### FAISS (RAG index)

- One vector per stored message (or per short chunk).
- Metadata: `contact_id`, `message_id`, `timestamp`.
- Search **filtered by contact** so Rahul’s memories never mix with a client’s.

### Embedding model (lightweight, local)

Recommended start:

| Choice | Why |
|--------|-----|
| `sentence-transformers/all-MiniLM-L6-v2` | Small (~80MB), fast on CPU, good enough for chat recall |
| Alternative | `paraphrase-MiniLM-L3-v2` if you want even lighter |

No cloud embedding API required.

---

## 5. Lightweight stack (confirmed)

| Piece | Tech | Role |
|-------|------|------|
| WhatsApp | Playwright → web.whatsapp.com | Receive / send |
| AI reply | Playwright → chatgpt.com | Generate reply (no API key) |
| Structured memory | **SQLite** | Contacts, profiles, full chat log |
| Semantic memory | **FAISS** | Search old chats by meaning |
| Embeddings | **MiniLM (local)** | Vectors for FAISS |
| Config | `.env` | Auto-reply, headless, paths |

Keep it local, cheap, and simple. Add heavier tools only if needed later.

---

## 6. Prompt shape (what GPT sees)

```
You are my personal WhatsApp assistant.
Reply as me to this contact. Output ONLY the WhatsApp message text.

Contact: {name}
Relationship: {relationship_type}
Language: {preferred_language}
Tone: {tone_notes}
OK topics: {topics_ok}
Avoid: {topics_avoid}
Owner notes: {notes}

Recent chat (oldest → newest):
{last_n_messages}

Relevant older context (from memory search):
{rag_snippets_or_none}

New incoming message:
{incoming_text}

Write the reply now:
```

---

## 7. Modes

| Mode | Behavior |
|------|----------|
| `AUTO_REPLY=true` (default for now) | GPT answer is sent on WhatsApp automatically |
| `AUTO_REPLY=false` | Generate + log answer only (manual send later, like wat-a-bot `/send`) |

Future (optional): draft + approve before send.

---

## 8. Build plan (next steps)

Do in this order — light first:

1. **SQLite schema** — contacts + messages (+ optional facts).
2. **Wire bot** — on every in/out message, save to SQLite; load profile + recent chat into GPT prompt.
3. **Profile editing** — simple way to set relationship / tone (CLI or small JSON/edit later dashboard).
4. **Embeddings + FAISS** — index new messages; on reply, retrieve top-k for that contact.
5. **Tune prompts** — shorter / better WhatsApp voice.
6. Later — manual approve, groups policy, media handling.

---

## 9. Out of scope (for now)

- OpenAI / paid chat APIs (we use Playwright ChatGPT).
- Heavy LLM local models (Ollama can be optional later).
- Full wat-a-bot dashboard / interviews (can port ideas later).
- Auto-learning full personality from history without any profile (start with simple profiles + chat memory).

---

## 10. One-line summary

**Goal:** Your WhatsApp assistant knows each contact and past chats (SQLite + light FAISS RAG), then replies via ChatGPT in the right tone for that person.

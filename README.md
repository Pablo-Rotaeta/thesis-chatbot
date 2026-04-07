# Thesis Chatbot — Skill-based vs Unconstrained LLM Dialog Management

**LIS080 Master's Thesis · Stockholm University · Pablo Rotaeta Pérez**

A comparative study of two dialog management approaches for task-oriented voice/chat systems, implemented as a Swedish-language car repair appointment booking chatbot.

---

## What This Is

This project implements and evaluates two LLM-based dialog management strategies:

| System | Description |
|--------|-------------|
| **Skill-based (constrained)** | Step-by-step YAML skill guides the LLM through a structured booking flow with slot extraction and validation |
| **Unconstrained (free LLM)** | Single system prompt, the LLM decides how to handle the conversation |

Both systems share the same frontend UI, database, and LLM adapter layer — only the dialog management logic differs. This makes them directly comparable for thesis evaluation.

---

## Architecture

```
User → Vercel (frontend) → ngrok tunnel → Local backend (FastAPI) → Gemini / OpenAI / Anthropic
                                                    ↓
                                              SQLite (session logs)
```

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | Next.js 14, React, TypeScript |
| Backend | Python 3.12, FastAPI, uvicorn |
| Dialog logic | Custom Python dialog managers |
| Skill definition | YAML |
| LLM providers | Google Gemini, OpenAI GPT, Anthropic Claude, Ollama |
| Database | SQLite (auto-created) |
| Hosting | Vercel (frontend) + ngrok (backend tunnel) |

---

## Project Structure

```
thesis-chatbot/
├── backend/
│   ├── app/
│   │   ├── main.py                      # FastAPI entry point
│   │   ├── routers/
│   │   │   ├── chat.py                  # /api/chat/* endpoints
│   │   │   ├── sessions.py              # /api/sessions/* endpoints
│   │   │   └── admin.py
│   │   ├── services/
│   │   │   ├── llm_adapters.py          # Gemini, OpenAI, Anthropic, Ollama
│   │   │   ├── dialog_managers.py       # Constrained & unconstrained systems
│   │   │   └── session_logger.py        # SQLite logging
│   │   └── skills/
│   │       └── boka_bilverkstad.yaml    # Skill definition (constrained system)
│   ├── data/
│   │   └── appointments.json           # Workshop locations & time slots
│   ├── requirements.txt
│   ├── .env.example
│   └── render.yaml
└── frontend/
    ├── src/
    │   ├── app/
    │   │   ├── page.tsx                 # Full chat UI + questionnaire
    │   │   ├── layout.tsx
    │   │   └── globals.css
    │   └── lib/
    │       └── api.ts                   # Backend API client
    ├── package.json
    └── next.config.js
```

---

## Prerequisites

- **Python 3.12** (not 3.13 or 3.14 — pydantic-core breaks on newer versions)
- **Node.js 18+**
- **ngrok 3.20+** — [ngrok.com/download](https://ngrok.com/download)
- A **Gemini API key** — [aistudio.google.com/apikey](https://aistudio.google.com/apikey) (free tier works for local testing; enable billing for user studies)

---

## Local Setup

### 1. Clone the repository

```bash
git clone https://github.com/Pablo-Rotaeta/thesis-chatbot.git
cd thesis-chatbot
```

### 2. Create virtual environment (Python 3.12)

```bash
py -3.12 -m venv venv

# Activate — Windows:
venv\Scripts\activate

# Activate — macOS/Linux:
source venv/bin/activate
```

### 3. Install backend dependencies

```bash
pip install -r backend/requirements.txt
```

### 4. Configure environment variables

```bash
cp backend/.env.example backend/.env
```

Edit `backend/.env`:
```
GEMINI_API_KEY=your_key_here
OPENAI_API_KEY=your_key_here      # optional
ANTHROPIC_API_KEY=your_key_here   # optional
```

> ⚠️ Never commit `.env` to version control.

### 5. Start the backend

```bash
cd backend
uvicorn app.main:app --port 8000 --reload
```

Visit [http://localhost:8000/docs](http://localhost:8000/docs) to verify and test endpoints interactively.

### 6. Install and start the frontend

```bash
cd frontend
npm install
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
npm run dev
```

Visit [http://localhost:3000](http://localhost:3000).

---

## Deployment (Sharing with Test Participants)

The frontend is hosted on Vercel. The backend runs locally and is exposed via ngrok.

### Start the tunnel

```bash
ngrok http 8000
# Copy the https://xxx.ngrok-free.app URL
```

### Update Vercel environment variable

Go to **Vercel → your project → Settings → Environment Variables** and set:

```
NEXT_PUBLIC_API_URL = https://xxx.ngrok-free.app
```

Then redeploy:

```bash
cd frontend
vercel --prod
```

> ⚠️ ngrok free tier generates a new URL on every restart. Update Vercel each time.

---

## Running Experiments

### Pre-configured URLs for participants

Append query parameters to lock in the system type:

```
# Skill-based:
https://thesis-chatbot.vercel.app?system=skill_based&provider=gemini

# Unconstrained:
https://thesis-chatbot.vercel.app?system=unconstrained&provider=gemini
```

### Viewing logged sessions

```
http://localhost:8000/api/sessions/
```

### Exporting data for analysis

```
http://localhost:8000/api/sessions/export/csv
```

---

## Evaluation

Three evaluation components are collected automatically and via questionnaire:

| Component | Metrics | Source |
|-----------|---------|--------|
| **A: Objective task metrics** | Task success rate, dialogue turns, repair turns | Interaction log + questionnaire |
| **B: UX evaluation** | UES-SF (focused attention, usability, reward factor), UMUX-Lite | Post-session questionnaire |
| **C: Structured error analysis** | Slot omission, incorrect value, premature closure, recovery failure | Interaction log |

---

## Supported LLM Providers

| Provider | Default model | Notes |
|----------|--------------|-------|
| Google Gemini | `gemini-2.0-flash-001` | Recommended — best Swedish support |
| OpenAI | `gpt-4o-mini` | Requires billing |
| Anthropic | `claude-haiku-4-5-20251001` | Requires billing |
| Ollama | `llama3` | Local only, free, no cloud needed |

To add a new provider, subclass `BaseLLMAdapter` in `backend/app/services/llm_adapters.py`.

---

## Daily Startup (during test sessions)

```bash
# Terminal 1 — backend
cd thesis-chatbot/backend && uvicorn app.main:app --port 8000

# Terminal 2 — tunnel
ngrok http 8000

# Terminal 3 — redeploy only if ngrok URL changed
cd thesis-chatbot/frontend && vercel --prod
```

---

## References

- Walker et al. (1997) — PARADISE framework for objective task metrics
- Kazi et al. (2024) — LLM user-agent simulation for automated evaluation
- O'Brien & Toms — User Engagement Scale Short Form (UES-SF)
- Finstad (2010) — UMUX-Lite usability scale
- McTear (2021) — *Conversational AI: Dialogue Systems, Conversational Agents, and Chatbots*
- Spring AI Blog (2026) — Generic Agent Skills
# ProfPilot AI

ProfPilot AI is a personalized, multi-agent AI workspace for university professors: one intelligent workspace for teaching, research, communication, and academic work.

The planned system brings together specialized agents for assessment generation, lecture and slide creation, course knowledge, email, planning, literature review, and research writing. A central orchestrator will coordinate them, with long-term memory that adapts to each professor.

> **Status:** the **Assessment Agent** is in place. A professor sets up an assessment (by hand, or by voice or text with **Set up with AI**), uploads course material and previous exams, and ProfPilot reads them, plans the exam for approval, writes it (all at once, or question by question with the professor), checks it, and exports the student paper and answer key as PDFs. The other agents are future work. See [Current status](#current-status).

## Architecture

The repository is a monorepo with two independent apps and a Supabase project:

```text
ProfPilot-AI/
├── frontend/                 Next.js web app (the professor's workspace)
│   └── src/
│       ├── proxy.ts          Refreshes the login session and redirects on every request
│       ├── app/              Pages: landing, /login, /signup, /auth/confirm, and the workspace:
│       │                     /workspace, /courses, /preferences, /assessments (list, new, [id],
│       │                     [id]/edit, [id]/plan, [id]/exam, [id]/builder)
│       ├── components/       Assessment form and uploads, Set up with AI, plan review, exam editor,
│       │                     Build with AI, voice call controls, preferences, layout, shadcn/ui
│       └── lib/
│           ├── supabase/     Supabase clients for the browser, the server and the proxy
│           ├── auth/         Login/signup/logout actions and the signed-in professor
│           ├── courses/      Course queries and actions
│           ├── assessments/  Draft rules, queries, and save/delete actions
│           ├── documents/    File rules and upload/remove actions
│           ├── api/          Calls to the FastAPI backend with the professor's access token
│           └── realtime/     The WebRTC voice call with OpenAI Realtime
├── backend/                  FastAPI service: the Assessment Agent
│   ├── app/
│   │   ├── main.py           Creates the app, configures CORS and errors, registers routes
│   │   ├── api/              HTTP routes
│   │   ├── core/             Settings and model names, token verification, errors
│   │   ├── db/               Supabase (PostgREST and Storage) as the signed-in professor
│   │   ├── ai/               The OpenAI client wrapper, prompts and tool definitions
│   │   ├── schemas/          Request/response models and structured AI outputs (Pydantic)
│   │   ├── domain/           Setup rules and question distributions
│   │   ├── services/         The agent's capabilities: reading files, interpreting, generating,
│   │   │                     reviewing, revising, voice, personalization, PDF export, jobs
│   │   ├── models/           Reserved
│   │   └── agents/           Reserved for the other agents and the orchestrator
│   └── tests/                pytest suites with in-memory fakes for Supabase and OpenAI
├── supabase/migrations/      Database schema and Storage bucket, as versioned SQL migrations
└── docs/                     Project documentation
```

The browser talks to the Next.js frontend, which uses **Supabase Auth** for accounts, **Supabase PostgreSQL** for data, and a private **Supabase Storage** bucket for uploaded files. Every table has Row Level Security, and the bucket has matching policies, so each professor can only ever reach their own rows and files.

For AI work, the browser calls the **FastAPI backend** with the professor's Supabase access token. FastAPI verifies the token against the project's public signing keys, then reads and writes Supabase **as that professor**, so Row Level Security applies to the backend too; no `service_role` key is used anywhere. FastAPI is the only part that holds the **OpenAI** key. For voice, it gives the browser a client secret that expires within two minutes, and the browser talks to OpenAI Realtime directly over WebRTC.

```text
Browser ──(publishable key, session)──────────────▶ Supabase (Auth, Postgres + RLS, Storage)
   │                                                      ▲
   ├──(Bearer <access token>)──▶ FastAPI ──(same token)───┘
   │                               └──(server-only key)──▶ OpenAI (Responses, Embeddings, Realtime secrets)
   └──(WebRTC, short-lived client secret)──────────────▶ OpenAI Realtime
```

- [docs/assessment-agent.md](docs/assessment-agent.md): the AI workflow end to end, the models, voice, security, the API and how it is tested
- [docs/assessment-setup.md](docs/assessment-setup.md): the assessment setup flow, drafts, uploaded files and the private bucket, and how to test it
- [docs/authentication.md](docs/authentication.md): sign-up, login, data isolation, and how FastAPI verifies professors

## Frontend stack

- Next.js 16 (App Router) with React 19 and TypeScript
- Tailwind CSS 4
- shadcn/ui components, built on Base UI, with Lucide icons
- Supabase (`@supabase/supabase-js`, `@supabase/ssr`) for authentication and data
- next-themes for light and dark mode
- ESLint

## Backend stack

- Python 3.11+
- FastAPI with Uvicorn
- Pydantic for data models and structured AI outputs, pydantic-settings for configuration
- The official OpenAI Python SDK: the Responses API with Structured Outputs, Embeddings, and Realtime client secrets
- PyJWT for verifying Supabase access tokens
- pypdf, python-pptx and python-docx for reading files; ReportLab for PDF export
- pgvector in Supabase for searching course material
- pytest and Ruff

The models are configured in one place, `backend/app/core/config.py`: `gpt-6.1-sol` for reasoning, `gpt-realtime-2.1` for voice, and `text-embedding-3-small` for search. Each can be overridden with an environment variable. See [docs/assessment-agent.md](docs/assessment-agent.md#models).

## Setting up Supabase

1. Create a project at [supabase.com](https://supabase.com).
2. Apply the migrations in `supabase/migrations/`, in order. They create the tables, Row Level Security, the private `assessment-files` Storage bucket with its policies, and (in `20261005203838_assessment_ai_workflow.sql`) the `vector` extension and the AI workflow's tables. Either paste each migration file into the dashboard's **SQL Editor** and run it, or use the Supabase CLI: `npx supabase login`, `npx supabase init`, `npx supabase link --project-ref <your-project-ref>`, then `npx supabase db push`.
3. In **Authentication > URL Configuration**, set the Site URL to `http://localhost:3000` and add `http://localhost:3000/**` to the Redirect URLs.
4. Optional but recommended: in the **Authentication** email templates, change the "Confirm signup" link to `{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=email`. With this template, confirmation links work in any browser, not only the one used to sign up.
5. Copy `frontend/.env.example` to `frontend/.env.local` and fill in the project URL and **publishable** key (dashboard: **Connect**, or **Settings > API Keys**), and `NEXT_PUBLIC_API_URL` (the backend's address, `http://localhost:8000` locally). Never put a secret key, the `service_role` key or the OpenAI key in the frontend.
6. The project must use **asymmetric JWT signing keys** (dashboard: **Project Settings > JWT Keys**; a project still on the legacy JWT secret can migrate there), so the backend can verify access tokens with the public keys alone.

After changing the database schema, regenerate the TypeScript types:
`npx supabase gen types typescript --project-id <your-project-ref> > frontend/src/lib/supabase/database.types.ts`

## Running the frontend

Requires Node.js 20.9 or newer, and the Supabase setup above.

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000. Other scripts: `npm run lint`, `npm test`, `npm run build`, and `npm start` (serves the production build). Type-check with `npx tsc --noEmit`.

`npm test` runs the component tests with Vitest in a real browser (Chromium, through Playwright); they need neither Supabase nor the backend. Install the browser once with `npx playwright install chromium`.

The setup form, drafts and uploads work without the backend. The AI features (reading files, Set up with AI, Improve with AI, the plan, the exam editor and export) need the backend running.

## Running the backend

Requires Python 3.11 or newer. Check with `python3 --version`; if it is older, use a specific version such as `python3.12`.

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Before starting it, copy `backend/.env.example` to `backend/.env` and fill in:

| Variable | Value |
| --- | --- |
| `SUPABASE_URL` | The project URL (the same public value as the frontend's) |
| `SUPABASE_PUBLISHABLE_KEY` | The publishable key (public; no secret Supabase key is needed) |
| `OPENAI_API_KEY` | Your OpenAI API key. **Secret**: it stays in `backend/.env`, which git ignores |
| `CORS_ORIGINS` | The frontend's origin, `http://localhost:3000` by default |

Optional overrides (`REASONING_MODEL`, `REALTIME_MODEL`, `REALTIME_VOICE`, `REALTIME_TRANSCRIPTION_MODEL`, `EMBEDDING_MODEL`, `REALTIME_CLIENT_SECRET_SECONDS`, `PDF_FALLBACK_FONT`) are listed in `.env.example`. For PDFs in scripts beyond Latin, Greek and common math symbols, point `PDF_FALLBACK_FONT` at a Unicode TrueType font such as DejaVu Sans.

The API runs at http://localhost:8000:

- `GET /`: API name and status
- `GET /health`: health check
- `/api/...`: the Assessment Agent; every route requires the professor's access token ([list](docs/assessment-agent.md#api))
- http://localhost:8000/docs: interactive API documentation

Run the checks from `backend/` with the virtual environment active:

```bash
pytest                              # OpenAI and Supabase are replaced by in-memory fakes: no API credit is used
ruff check . && ruff format --check .
```

An optional live smoke test makes two small real OpenAI calls (one structured output, one voice credential). It only runs when asked:

```bash
PROFPILOT_LIVE_TESTS=1 pytest tests/test_live_smoke.py
```

Real `.env` files are ignored by git; never commit secrets or API keys.

## Current status

**In place**

- Monorepo structure for the frontend, backend, database migrations, and docs
- Email and password sign-up (with email confirmation), login, and logout using Supabase Auth
- Database schema for professor profiles, courses, exam projects and uploaded documents, with Row Level Security on every table
- A profile created automatically for every new professor
- Protected Professor Workspace with a sidebar (Home, Courses, Assessments), showing the professor's name, email and institution
- Course management: add, edit and delete courses
- Assessment setup ([details](docs/assessment-setup.md)):
  - the full form: course, name, course material, previous assessments, exam design, notes, attachments, and instructions;
  - drafts that can be saved, reopened, edited and deleted;
  - an overview page for each assessment, and the Assessments list
- File uploads to a private Supabase Storage bucket, with per-professor access policies
- Landing page, light and dark mode, and a responsive layout for phones, tablets and desktops
- The Assessment Agent ([details](docs/assessment-agent.md)):
  - FastAPI verifies each professor's Supabase access token and works as that professor, under Row Level Security;
  - uploaded files are read in the background (PDF, including scanned pages, PPTX, DOCX, TXT and images), summarized, and indexed with pgvector;
  - a style profile of the professor's previous exams;
  - **Set up with AI**: fill in the setup form by voice (OpenAI Realtime over WebRTC) or text;
  - **Improve with AI**: an enhanced prompt and an ExamSpec showing where each setting came from, to review, edit, regenerate and approve, and marked out of date when the setup or files change;
  - **Generate full exam** (plan, questions and answer keys, equivalent versions, AI quality check), or **Build with AI** question by question, by voice or text;
  - an exam editor with manual edits, AI revisions and quick actions, approve and lock, revision history and restore, version sync, reordering and distribution warnings;
  - final review checks and PDF export of the student exam and the answer key, per version;
  - personalization from finalized exams and approved revisions only, which the professor can switch off or clear

**Not started**

- Password reset and profile editing
- The other AI agents (lectures and slides, course knowledge, email, planning, research) and the orchestrator

# ProfPilot AI

ProfPilot AI is a personalized, multi-agent AI workspace for university professors: one intelligent workspace for teaching, research, communication, and academic work.

The planned system brings together specialized agents for assessment generation, lecture and slide creation, course knowledge, email, planning, literature review, and research writing. A central orchestrator will coordinate them, with long-term memory that adapts to each professor.

> **Status:** early foundation. No AI features exist yet. See [Current status](#current-status).

## Architecture

The repository is a monorepo with two independent apps:

```text
ProfPilot-AI/
├── frontend/                 Next.js web app (the professor's workspace)
│   └── src/
│       ├── app/              Pages, root layout, global styles
│       ├── components/       Layout, theme, and shadcn/ui components
│       └── lib/              Shared helpers
├── backend/                  FastAPI service (will host the AI agents)
│   ├── app/
│   │   ├── main.py           Creates the app, configures CORS, registers routes
│   │   ├── api/              HTTP routes
│   │   ├── core/             Configuration from environment variables
│   │   ├── schemas/          Request and response models (Pydantic)
│   │   ├── models/           Database models (empty for now)
│   │   ├── services/         Business logic (empty for now)
│   │   └── agents/           AI agents (empty for now)
│   └── tests/                API tests (pytest)
├── supabase/migrations/      Database migrations (empty for now)
└── docs/                     Project documentation
```

The browser loads the Next.js frontend, which will call the FastAPI backend over HTTP. The backend already accepts browser requests from `http://localhost:3000` (CORS). Supabase and AI providers are planned but not connected yet.

## Frontend stack

- Next.js 16 (App Router) with React 19 and TypeScript
- Tailwind CSS 4
- shadcn/ui components, built on Base UI, with Lucide icons
- next-themes for light and dark mode
- ESLint

## Backend stack

- Python 3.11+
- FastAPI with Uvicorn
- Pydantic for data models, pydantic-settings for configuration
- pytest for tests

Planned: Supabase (PostgreSQL, Auth, Storage, Realtime, pgvector) and the OpenAI API, with room to swap in other AI providers.

## Running the frontend

Requires Node.js 20.9 or newer.

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000. Other scripts: `npm run lint`, `npm run build`, and `npm start` (serves the production build).

## Running the backend

Requires Python 3.11 or newer. Check with `python3 --version`; if it is older, use a specific version such as `python3.12`.

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

The API runs at http://localhost:8000:

- `GET /`: API name and status
- `GET /health`: health check
- http://localhost:8000/docs: interactive API documentation

Run the tests from `backend/` with the virtual environment active:

```bash
pytest
```

The defaults work for local development. To change settings, copy `backend/.env.example` to `backend/.env`. Real `.env` files are ignored by git; never commit secrets or API keys.

## Current status

**In place**

- Monorepo structure for the frontend, backend, database migrations, and docs
- Frontend shell with branding, responsive layout, light and dark mode, and a placeholder for the Professor Workspace
- Backend API with `GET /` and `GET /health`, CORS for the local frontend, environment-based configuration, and tests

**Not started**

- Frontend-to-backend communication
- Supabase database, authentication, and storage
- AI agents, the orchestrator, and professor personalization

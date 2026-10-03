# ProfPilot AI

ProfPilot AI is a personalized, multi-agent AI workspace for university professors: one intelligent workspace for teaching, research, communication, and academic work.

The planned system brings together specialized agents for assessment generation, lecture and slide creation, course knowledge, email, planning, literature review, and research writing. A central orchestrator will coordinate them, with long-term memory that adapts to each professor.

> **Status:** professors can sign up, manage their courses, and set up assessments, with course material, previous exams and attachments uploaded and saved as drafts. No AI features exist yet: AI exam generation is the next phase. See [Current status](#current-status).

## Architecture

The repository is a monorepo with two independent apps and a Supabase project:

```text
ProfPilot-AI/
├── frontend/                 Next.js web app (the professor's workspace)
│   └── src/
│       ├── proxy.ts          Refreshes the login session and redirects on every request
│       ├── app/              Pages: landing, /login, /signup, /auth/confirm, and the workspace:
│       │                     /workspace, /courses, /assessments (list, new, [id], [id]/edit)
│       ├── components/       Assessment form and uploads, courses, auth forms, layout, shadcn/ui
│       └── lib/
│           ├── supabase/     Supabase clients for the browser, the server and the proxy
│           ├── auth/         Login/signup/logout actions and the signed-in professor
│           ├── courses/      Course queries and actions
│           ├── assessments/  Draft rules, queries, and save/delete actions
│           └── documents/    File rules and upload/remove actions
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
├── supabase/migrations/      Database schema and Storage bucket, as versioned SQL migrations
└── docs/                     Project documentation
```

The browser talks to the Next.js frontend, which uses **Supabase Auth** for accounts, **Supabase PostgreSQL** for data, and a private **Supabase Storage** bucket for uploaded files. Every table has Row Level Security, and the bucket has matching policies, so each professor can only ever reach their own rows and files. The FastAPI backend is not connected yet; it will handle AI work later.

- [docs/assessment-setup.md](docs/assessment-setup.md): the assessment setup flow, drafts, uploaded files and the private bucket, and how to test it
- [docs/authentication.md](docs/authentication.md): sign-up, login and data isolation

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
- Pydantic for data models, pydantic-settings for configuration
- pytest for tests

Planned: Supabase Realtime and pgvector, and the OpenAI API, with room to swap in other AI providers.

## Setting up Supabase

1. Create a project at [supabase.com](https://supabase.com).
2. Apply the migrations in `supabase/migrations/`, in order. They create the tables, Row Level Security, and the private `assessment-files` Storage bucket with its policies. Either paste each migration file into the dashboard's **SQL Editor** and run it, or use the Supabase CLI: `npx supabase login`, `npx supabase init`, `npx supabase link --project-ref <your-project-ref>`, then `npx supabase db push`.
3. In **Authentication > URL Configuration**, set the Site URL to `http://localhost:3000` and add `http://localhost:3000/**` to the Redirect URLs.
4. Optional but recommended: in the **Authentication** email templates, change the "Confirm signup" link to `{{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=email`. With this template, confirmation links work in any browser, not only the one used to sign up.
5. Copy `frontend/.env.example` to `frontend/.env.local` and fill in the project URL and **publishable** key (dashboard: **Connect**, or **Settings > API Keys**). Never put a secret key or the `service_role` key in the frontend.

After changing the database schema, regenerate the TypeScript types:
`npx supabase gen types typescript --project-id <your-project-ref> > frontend/src/lib/supabase/database.types.ts`

## Running the frontend

Requires Node.js 20.9 or newer, and the Supabase setup above.

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
- Backend API with `GET /` and `GET /health`, CORS for the local frontend, environment-based configuration, and tests

**Next phase: AI generation**

- Turning a saved draft into an exam: Prompt Interpreter, then Enhanced Prompt, then ExamSpec, then the generated exam
- Reading the uploaded files (parsing, then course knowledge search with pgvector)

**Not started**

- Password reset and profile editing
- Frontend-to-backend communication, and verifying Supabase tokens in FastAPI
- The other AI agents, the orchestrator, and professor personalization

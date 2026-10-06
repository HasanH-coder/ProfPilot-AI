# Authentication and data security

How professors sign up and log in, and how ProfPilot AI keeps every professor's data private.

## Sign-up and login

1. **Sign up** (`/signup`). The form calls the `signup` Server Action in `frontend/src/lib/auth/actions.ts`. It validates the input on the server, then sends the email, password and full name to Supabase Auth.
2. **Profile.** Supabase adds the new user to `auth.users`. A database trigger (`handle_new_user`) immediately creates the matching row in `public.profiles` with the full name.
3. **Email confirmation.** Supabase emails a confirmation link to `/auth/confirm`, which verifies it, signs the professor in, and opens `/workspace`. If email confirmation is turned off in Supabase, sign-up goes straight to the workspace.
4. **Log in** (`/login`). The `login` Server Action checks the email and password with Supabase Auth.
5. **Session.** Supabase keeps the session in cookies. `frontend/src/proxy.ts` runs before every request and refreshes the session before it expires.
6. **Log out.** The `logout` Server Action ends the session in this browser and returns to `/login`.

## Three layers of protection

| Layer | Where | What it does |
| --- | --- | --- |
| Proxy | `frontend/src/proxy.ts` | Sends signed-out visitors from `/workspace` to `/login`, and signed-in professors from `/login` and `/signup` to `/workspace`. A convenience only. |
| Server check | `frontend/src/lib/auth/current-professor.ts` | Every workspace page verifies the session token (`getClaims()`) before rendering, and redirects to `/login` if it is missing or invalid. |
| Row Level Security | `supabase/migrations/` | Postgres itself only returns or changes rows owned by the signed-in professor (`auth.uid()`), even if someone sends a forged request. |

The browser only ever receives the **publishable** key, which is designed to be public. The secret / `service_role` key bypasses Row Level Security and must never leave the server. ProfPilot doesn't use it at all: the FastAPI backend works with the professor's own access token (see [Protecting the FastAPI endpoints](#protecting-the-fastapi-endpoints)).

## Data model

- `profiles`: one row per professor (`id` is the Supabase user id), with full name, institution and department.
- `courses`: owned by a professor through `professor_id`.
- `exam_projects`: owned by a professor through `professor_id`, optionally linked to one of their courses.
- `documents`: one row per uploaded file, owned through `professor_id` and linked to an exam project. The files themselves are in the private `assessment-files` Storage bucket. See [Assessment setup](assessment-setup.md).
- The AI workflow's tables, each owned through `professor_id` (see [Assessment Agent](assessment-agent.md#data-model)):
  - per assessment: `document_chunks`, `assessment_style_profiles`, `exams` and their `exam_versions`, `exam_sections`, `exam_questions` and `question_revisions`, `ai_runs`, `exam_builder_messages`;
  - per professor: `professor_preferences`, `preference_signals`.

Deleting a professor's account deletes their profile, courses, exam projects and all the rows above. Deleting a course keeps its exam projects and documents but unlinks them. Deleting an exam project deletes its documents' rows and everything the AI workflow created for it.

## Row Level Security policies

Policies apply to signed-in users (`authenticated`) only. Signed-out visitors (`anon`) have no access to any table.

| Table | Read | Create | Update | Delete |
| --- | --- | --- | --- | --- |
| `profiles` | own profile | only by the sign-up trigger | own profile | only with the account |
| `courses` | own courses | for themselves | own courses | own courses |
| `exam_projects` | own projects | for themselves, linked only to their own courses | own projects, linked only to their own courses | own projects |
| `documents` | own documents | for themselves, in their own Storage folder, linked only to their own course and exam project | same rules as create | own documents |
| Storage `assessment-files` | files in their own folder | into their own folder only | not allowed (files are never overwritten) | files in their own folder |
| `document_chunks` | own chunks | for their own documents | not allowed | own chunks |
| `assessment_style_profiles`, `ai_runs` | own rows | for their own exam projects | own rows | own rows |
| `exams` | own exams | for their own exam projects | own exams | own exams |
| `exam_versions`, `exam_sections` | own rows | in their own exams | own rows | own rows |
| `exam_questions` | own questions | in their own exam, version and section (and an image from their own files) | same rules as create | own questions |
| `question_revisions` | own revisions | for their own questions | not allowed (history is never rewritten) | own revisions |
| `exam_builder_messages` | own messages | for their own exams | not allowed | own messages |
| `professor_preferences` | own row | for themselves | own row | own row |
| `preference_signals` | own signals | for themselves, linked only to their own exam projects | not allowed | own signals |

"Own" means the row's `id` or `professor_id` equals `auth.uid()`. `professor_id` defaults to the signed-in professor, so the browser never needs to send it, and a forged value is rejected. For the AI tables, every parent row (exam project, exam, version, section, question, document) must belong to the same professor too, so one professor can't attach rows to another's data.

The two database functions the backend calls, `match_document_chunks` (course-material search) and `reorder_exam_questions`, are `security invoker`: they run with the caller's permissions, so Row Level Security applies inside them.

## Protecting the FastAPI endpoints

The frontend calls FastAPI for AI work with the professor's Supabase access token (`Authorization: Bearer <token>`, from `frontend/src/lib/api/client.ts`). Every route except `GET /` and `GET /health` depends on `get_current_professor` (`backend/app/core/security.py`), which verifies the token before the route runs:

- **Signature**: checked locally against the project's public keys at `<SUPABASE_URL>/auth/v1/.well-known/jwks.json` (asymmetric keys only: ES256, RS256 or EdDSA). No secret is needed. Keys are cached for 10 minutes and refetched when a token names an unknown key.
- **Claims**: the issuer must be the project's `<SUPABASE_URL>/auth/v1`, the audience `authenticated`, the role `authenticated`; `exp`, `iat` and `sub` are required; anonymous users are refused; `sub` must be a UUID.
- **Identity**: the professor's id comes only from the verified `sub` claim. The API never accepts a professor or user id from the request body or URL.

The backend then calls Supabase's REST and Storage APIs **with that same token** and the publishable key (`backend/app/db/supabase.py`). Postgres sees the professor as `auth.uid()`, so every query and upload is subject to the policies above; the backend has no way around them. Rows that belong to someone else simply aren't found, and the API answers `404`, never revealing whether they exist. A missing, expired or invalid token gets `401`.

The browser refreshes its session before starting a long AI job, so the token outlives the job in normal use. The OpenAI key is only in `backend/.env` and never sent to the browser; voice calls use short-lived client secrets ([details](assessment-agent.md#realtime-security)).

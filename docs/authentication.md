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

The browser only ever receives the **publishable** key, which is designed to be public. The secret / `service_role` key bypasses Row Level Security and must never leave the server.

## Data model

- `profiles`: one row per professor (`id` is the Supabase user id), with full name, institution and department.
- `courses`: owned by a professor through `professor_id`.
- `exam_projects`: owned by a professor through `professor_id`, optionally linked to one of their courses.

Deleting a professor's account deletes their profile, courses and exam projects. Deleting a course keeps its exam projects but unlinks them.

## Row Level Security policies

Policies apply to signed-in users (`authenticated`) only. Signed-out visitors (`anon`) have no access to any table.

| Table | Read | Create | Update | Delete |
| --- | --- | --- | --- | --- |
| `profiles` | own profile | only by the sign-up trigger | own profile | only with the account |
| `courses` | own courses | for themselves | own courses | own courses |
| `exam_projects` | own projects | for themselves, linked only to their own courses | own projects, linked only to their own courses | own projects |

"Own" means the row's `id` or `professor_id` equals `auth.uid()`. `professor_id` defaults to the signed-in professor, so the browser never needs to send it, and a forged value is rejected.

## Later: protecting FastAPI endpoints

Once the frontend calls FastAPI for AI work, each request will carry the professor's Supabase access token (`Authorization: Bearer <token>`). A shared FastAPI dependency in `backend/app/api/` will verify the token before any protected route runs, and read the professor's id from its `sub` claim:

- With asymmetric JWT signing keys (recommended by Supabase), the token is verified locally against the project's public keys at `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`. No secret is needed.
- Otherwise, FastAPI can ask Supabase Auth to validate the token.

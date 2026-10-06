# The Assessment Agent

How ProfPilot turns a professor's assessment setup into a reviewed, exportable exam: what the AI does at each step, which model it uses, where the data lives, and how each professor's work stays private.

The setup form itself (fields, drafts, uploads, the private bucket) is described in [Assessment setup](assessment-setup.md). Sign-in and Row Level Security are in [Authentication and data security](authentication.md).

## The professor's journey

| Step | Where | What happens |
| --- | --- | --- |
| 1. Set up | `/workspace/assessments/new`, `[id]/edit` | The setup form. Optional **Set up with AI** fills it by voice or text. Uploaded files are read in the background. |
| 2. Improve with AI | the form, or the overview's **AI assessment** card | The Prompt Interpreter reads everything and writes an **Enhanced prompt** and an **ExamSpec**. |
| 3. Review the plan | `[id]/plan` | The professor checks the plan, edits the enhanced prompt, regenerates or approves it. |
| 4. Choose a mode | `[id]/plan` | **Generate full exam** (ProfPilot writes everything) or **Build with AI** (question by question, with the professor). |
| 5. Edit | `[id]/exam` or `[id]/builder` | The exam editor: manual edits, AI revisions, approve and lock, history, versions, quality check. |
| 6. Final review and export | `[id]/exam` | Readiness checks, **Mark as final**, and PDF export of the student paper and the answer key. |
| Preferences | `/workspace/preferences` | The professor's own notes, what ProfPilot has learned from accepted work, and a switch to turn learning off. |

Every AI step shows what it is doing ("Reading your course material…", "Planning the assessment…"), never a made-up percentage, and every failure ends in a clear message and a way forward.

## Architecture

```text
Browser (Next.js, professor's session)
 │
 ├── Supabase (publishable key + professor's session)  ── setup form, uploads, drafts
 │
 ├── FastAPI  (Authorization: Bearer <Supabase access token>)
 │     ├── verifies the token against the project's public JWKS
 │     ├── Supabase PostgREST + Storage, as the professor (Row Level Security applies)
 │     └── OpenAI: Responses API (reasoning), Embeddings, Realtime client secrets
 │
 └── OpenAI Realtime over WebRTC  (short-lived client secret minted by FastAPI)
       voice in/out; tool calls are executed by FastAPI, never by the browser alone
```

- **No service_role key anywhere.** FastAPI calls Supabase with the professor's own access token and the publishable key, so the database's Row Level Security policies decide every read and write, exactly as they do for the browser.
- **The OpenAI key stays on the server** (`backend/.env`). The browser never sees it; for voice it receives a client secret that expires within two minutes (a call must start before then).
- **Long work runs as background jobs** (`ai_runs` rows) that the browser polls. A job records its stage and a heartbeat, so a crashed job shows as interrupted instead of running forever.

### Backend layout

```text
backend/app/
├── main.py              App, CORS, error handlers, routers
├── core/                config.py (all settings and model names), security.py (token verification), errors.py
├── db/supabase.py       PostgREST + Storage client that acts as the signed-in professor
├── ai/                  openai_service.py (the only code that talks to OpenAI), prompts.py, tools.py
├── schemas/             ai.py (structured-output schemas), api.py (request/response models)
├── domain/              setup.py (the setup form's rules), distribution.py (format/difficulty by marks)
├── services/            One service per capability (below)
└── api/                 assessments.py, exams.py, assistant.py, preferences.py, deps.py
```

| Service | Responsibility |
| --- | --- |
| `assessment_context` | Loads the assessment, its files, style profile and preferences; renders them for prompts |
| `document_parsers`, `document_ingestion` | Text extraction, OCR, chunking, embeddings, per-file summaries |
| `document_retrieval` | Similarity search over course material |
| `style_analysis` | The previous-exam style profile |
| `prompt_interpreter` | Enhanced prompt and ExamSpec; approval and staleness |
| `exam_generation` | Planning, question writing, versions |
| `exam_review` | Deterministic checks plus the AI quality check, with safe automatic fixes |
| `exam_store` | Exams, versions, sections, questions and revisions in the database |
| `question_revision` | Manual edits, AI revisions, presets, approve/lock, delete, reorder, restore, version sync |
| `exam_builder` | Build with AI: the shared tool set for chat and voice |
| `setup_assistant` | Set up with AI: tools that change the setup form |
| `realtime_session` | Voice credentials (client secrets) and their rate limit |
| `personalization` | Explicit preferences and what is learned from accepted work |
| `readiness`, `pdf_export` | Final review and the PDFs |
| `jobs` | Background jobs, stages, heartbeats |

## Models

All model names are in `backend/app/core/config.py` and can be overridden with an environment variable of the same name.

| Setting | Default | Used for |
| --- | --- | --- |
| `REASONING_MODEL` | `gpt-6.1-sol` | Everything that reads or writes text: file summaries, OCR of scanned PDFs, reading images, style analysis, the Prompt Interpreter, planning, question writing, versions, the quality check, AI revisions, answer keys, Build with AI and the text chat of Set up with AI |
| `REALTIME_MODEL` | `gpt-realtime-2.1` | Voice: Set up with AI and Build with AI |
| `REALTIME_TRANSCRIPTION_MODEL` | `gpt-live-transcribe` | Live captions during a voice call |
| `REALTIME_VOICE` | `marin` | The assistant's voice |
| `EMBEDDING_MODEL` | `text-embedding-3-small` (1536 dimensions) | Searching course material. The dimension must match the `vector(1536)` column. |

Reasoning calls use the **Responses API** with **Structured Outputs** (`responses.parse` with a Pydantic model), so every reply has exactly the expected shape. Each call sets a reasoning effort that suits it (low for file summaries and chat; medium for style analysis and versions; high for planning, question writing, answer keys and the quality check; the interpreter and AI revisions use high for complex requests and medium otherwise) and `store=False`. Tool use (Build with AI, the text chat) uses strict function tools, replaying reasoning items between rounds.

`OpenAIService` (`backend/app/ai/openai_service.py`) wraps every call:

- **Validation and one repair.** A reply that fails the schema or the business rules (percentages that don't add up, an answer key that isn't a choice, duplicate questions) is sent back once with the problems listed. If it is still wrong, the step fails with a clear message; nothing invalid is saved.
- **Truncation, refusals and safety filters** are detected and turned into clear messages.
- **Errors are translated** into safe messages (`ai_busy`, `ai_timeout`, `ai_unavailable`, …). Raw OpenAI errors, prompts and keys are never returned or logged. Usage logs record only the purpose, token counts and time.
- **Concurrency is bounded** (`MAX_CONCURRENT_GENERATION_CALLS`, default 3); if one call in a group fails, the others are cancelled.

## Data model

Migration `supabase/migrations/20261005203838_assessment_ai_workflow.sql` adds:

| Table / column | Purpose |
| --- | --- |
| `documents.processing_status`, `processing_error`, `processed_at`, `content_sha256`, `page_count`, `extracted_characters`, `summary` | Whether each file has been read, and what it contains |
| `document_chunks` | Text chunks with `embedding vector(1536)` (HNSW cosine index). Category and assessment are copied from the file by a trigger, so they can't disagree. |
| `match_document_chunks(...)` | Similarity search (`security invoker`, so Row Level Security applies) |
| `exam_projects.spec_status`, `spec_generated_at`, `spec_approved_at` | The plan's state: `none`, `draft`, `approved`, `stale` |
| `assessment_style_profiles` | The cached previous-exam style profile, keyed by a hash of the files it came from |
| `exams` | One per assessment: mode, status, the approved spec it was built from, the plan, the quality-check result, `finalized_at` |
| `exam_versions`, `exam_sections`, `exam_questions` | The exam. Equivalent questions in different versions share a `slot_id`. |
| `question_revisions` | Every earlier state of a question, with what changed it |
| `ai_runs` | Background jobs. A partial unique index allows only one running job of each kind per assessment. |
| `professor_preferences`, `preference_signals` | Explicit preferences and the accepted work they are learned from |
| `exam_builder_messages` | The Build with AI conversation (text and voice transcripts) |

Database rules that hold whatever the API sends:

- A multiple-choice question has 2–8 choices and its `correct_choice` is one of them (`CHECK`).
- Question positions are unique within a version, and section positions within an exam (deferrable, so `reorder_exam_questions` can swap them in one statement).
- Questions, sections and versions must belong to the same exam (composite foreign keys).
- Changing any setup field, or adding or removing a file, marks an existing plan **stale** (triggers `mark_exam_spec_stale` and `mark_exam_spec_stale_for_documents`).
- Deleting an assessment deletes everything above with it (`on delete cascade`).

Every new table has Row Level Security. Each policy checks `professor_id = auth.uid()` and that every parent row (assessment, exam, version, section, file) belongs to the same professor. Chunks, revisions, signals and messages can't be updated at all, only added or deleted. See [Authentication and data security](authentication.md#row-level-security-policies).

## Reading the uploaded files

Files are read in the background as soon as they are uploaded (`POST /api/assessments/{id}/documents/processing`). The form shows **Being read…**, **Read by ProfPilot**, or **Couldn't be read** with the reason and **Read again**.

1. **Download** the file from the private bucket as the professor.
2. **Reuse.** If the professor already processed a file with the same SHA-256 hash, its chunks and summary are copied: no new AI cost.
3. **Extract text.** PDF page by page (pypdf), PPTX slides, tables and speaker notes, DOCX headings and tables, TXT in common encodings. Damaged, password-protected and empty files fail with a specific message.
4. **Scanned PDFs** (at least half the pages without a text layer): those pages are transcribed by the reasoning model, eight pages per request, up to `MAX_OCR_PAGES` (default 30) pages.
5. **Images** are described by the reasoning model (what they show, any text, labels and data).
6. **Chunk** (about 2,400 characters with 300 overlapping, labelled "Page 3", "Slides 1–3" or "Section: …"), **embed** in batches, and store.
7. **Summarize** the file (title, summary, topics with locations).

The three categories stay separate:

- **Course material** is the only source searched for question content.
- **Previous assessments** are used only for the style profile, never copied into new questions.
- **Attachments** are described to the interpreter and planner, and an image can be attached to a question as its figure.

### Retrieval

`DocumentRetrievalService.search` embeds one or more queries (topics, concepts, the professor's request), calls `match_document_chunks` for course material only, merges the results by best similarity, and fits them into a character budget. Excerpts are labelled `S1`, `S2`, … so questions can cite them (`source_excerpt_ids`). A citation is kept only if it matches an excerpt that was actually provided, and is shown under the question as "Based on: Lecture 3.pdf, Slides 4–6".

## Previous-exam style analysis

When previous assessments are uploaded, `style_analysis` builds a **style profile**: common question types, typical length, difficulty, conceptual vs computational, recall vs application, wording, scenarios, sub-questions, section structure, grading patterns, recurring preferences with evidence, and limitations. It is cached by a hash of the files and their contents and only rebuilt when they change. The form notes it under **Previous assessments** ("ProfPilot analysed 2 previous exams: …"), and so does the overview's **AI assessment** card.

The profile guides style only. The professor's settings and instructions always win, and questions are never copied from a previous exam.

## The Prompt Interpreter

**Improve with AI** saves the form, then starts an interpretation job. The interpreter reads:

- the setup (course, name, duration, format, difficulty, versions, notes, instructions);
- summaries of every file and the most relevant course-material excerpts;
- the style profile;
- the professor's preferences.

It returns an **enhanced prompt** (a clear, complete brief the professor can edit) and an **ExamSpec** (`backend/app/schemas/ai.py`):

| Group | Fields |
| --- | --- |
| Identity | `assessment_title`, `course`, `assessment_type` |
| Format | `duration_minutes`, `versions`, `total_points`, `question_count_min/max`, `question_format` (MCQ / subjective %), `difficulty` (easy / medium / hard %), `sections` |
| Content | `coverage` (topics with emphasis and source files), `learning_emphasis`, `question_style`, `professor_notes`, `professor_preferences_applied`, `source_material_guidance`, `previous_exam_style_guidance`, `constraints` |
| Transparency | `assumptions`, `warnings`, `generation_instructions` |

Every setting carries its **provenance**: `professor` ("Your setting"), `ai_inferred` ("Inferred" from the material or instructions) or `ai_assumption` ("Assumption", where generation needed a value nobody gave). Values the professor set in the form are re-applied in code after the model replies, so the AI can never override them; it can only fill gaps and flag conflicts in `warnings`.

### Plan review and approval

The plan page (`[id]/plan`) shows the spec with provenance badges, the assumptions and warnings, and the enhanced prompt.

- **Edit** the enhanced prompt; the plan goes back to **draft**.
- **Regenerate** the plan.
- **Approve & continue**; generation only starts from an approved plan.

A plan becomes **out of date** when the setup changes, a file is added or removed, or a file that failed to read is now ready. The page then explains what changed and asks for a new plan; an out-of-date plan can't be approved. The staleness check runs both in the database (triggers) and in the API (`stale_reasons`).

## Generating the exam

### Generate full exam

`POST /api/assessments/{id}/exam` with `mode: "full"` starts a job with visible stages:

1. **Planning.** The reasoning model writes an exam plan: sections, and for each question its type, difficulty, points, topic, learning objective, cognitive level, source hint and estimated time. The plan is checked in code (counts, total points, format and difficulty split by marks within 6 percentage points, duration) and repaired once if needed.
2. **Writing questions and answer keys.** Questions are written in batches of up to four per section, several batches at once, each grounded in retrieved course-material excerpts. Each batch is validated before it is saved: the planned type, difficulty and points; a valid answer for every MCQ; an answer, a worked solution and a rubric whose points add up; no near-duplicates of other questions.
3. **Creating equivalent versions** (if more than one). Each question of version A gets an equivalent variant per version, with the same topic, type, difficulty and points; equivalents share a `slot_id`.
4. **Running the AI quality check** (below).
5. **Finalizing.**

If a step fails, the questions already saved are kept and the exam shows **Continue generation**, which resumes where it stopped (`resume: true`). Replacing an existing exam requires an explicit confirmation (`replace: true`).

### Build with AI

`mode: "interactive"` creates an empty exam and opens `[id]/builder`: the exam on one side, a conversation with ProfPilot on the other (tabs on a phone). The professor can type or talk. Both use the same tools, executed by FastAPI:

`get_exam_state`, `generate_next_question`, `revise_question`, `approve_question`, `unlock_question`, `delete_question`, `move_question`, `review_question`, `regenerate_solution`, `finish_exam`.

The assistant follows the approved plan, suggests the next question, and never changes an approved (locked) question. **Finish** creates the other versions and runs the quality check, then opens the exam editor.

## The quality check

`ExamReviewService.review` combines:

- **Deterministic checks:** structure, answer keys, edited questions whose key may be out of date, near-duplicates, the format and difficulty split per version, and whether the versions match.
- **An AI review** (high reasoning effort) of every question against the course excerpts: answer-key errors, ambiguity, missing information, impossible or unanswerable questions, difficulty, clarity, rubric and solution problems, duration.

Only **safe fixes** are applied automatically: correcting an answer key, rewording for clarity (only if the new text is still close to the old), fixing a solution or rubric. Locked questions are never changed. Each fix is recorded in the question's history as "Fixed by the quality check", so it can be undone. Everything else is reported for the professor to decide.

## The exam editor

Each question card supports:

- **Edit manually**: text, choices, correct answer, answer, solution, rubric, points, difficulty. Only the changed fields are sent. If the question changes but its answer key doesn't, it is flagged **Check answer key**.
- **Ask AI** with any instruction, or a quick action: **Make harder**, **Make easier**, **More application-based**, **Make clearer**, **Shorten**, **Generate alternative**, **Replace question**. The revised question is validated like a generated one, and its answer key is rewritten with it.
- **Regenerate answer key** for the current text.
- **Approve** (locks the question against AI and edits) and **Unlock**.
- **Revision history**: every earlier state, with its source ("Changed with AI", "Edited by you", "Fixed by the quality check", "Answer key regenerated", "Earlier version restored"). **Restore** brings one back and keeps the current state in the history too.
- **Update other versions to match** after changing a question, so its equivalents in the other versions follow (approved ones are left alone).
- **Move up** / **Move down** within its section, in the version on screen.
- **Delete question**, which removes it from every version so they stay equivalent.

Distribution warnings (format, difficulty, total time) update as questions change, per version.

## Multiple versions

Versions are labelled A, B, C… Equivalent questions share a `slot_id` and are written to test the same thing at the same difficulty and points with different details. The quality check compares versions, and the final review blocks silent mismatches (a serious issue that needs confirmation).

## Voice: Set up with AI and Build with AI

1. The browser asks FastAPI for a voice credential: `POST /api/realtime/client-secrets` with the purpose (`setup` or `builder`) and the assessment or exam id.
2. FastAPI checks that the professor owns it, builds the session (model, voice, instructions, tools, semantic voice activity detection, transcription), and asks OpenAI for a **client secret** (`client.realtime.client_secrets.create`). It returns only the secret, its expiry and the model name.
3. The browser opens a **WebRTC** connection directly to OpenAI (`POST https://api.openai.com/v1/realtime/calls` with the SDP offer and the client secret), with the microphone as input and an `oai-events` data channel.
4. When the model calls a tool, the browser sends it to FastAPI (`/api/setup-assistant/tools/{name}` or `/api/exams/{id}/builder/tools/{name}`), which validates the arguments, checks ownership, applies the change and returns the result. The browser passes the result back to the model. The browser never applies an AI change on its own authority.

**Set up with AI** fills the form live. Each change is highlighted, listed in a change feed, and saved to the same draft as the form (an autosave). The assistant asks about missing settings, confirms what it changed, and never invents files or courses.

**Fallbacks.** If voice isn't supported, the microphone is blocked or missing, or the connection fails, the panel says why and offers the **text chat**, which uses the same tools through `POST /api/setup-assistant/messages` or `/api/exams/{id}/builder/messages`. The call has **Mute** / **Unmute** and **End call**, the call's duration, who is speaking, and live captions when transcription is available.

### Realtime security

- The OpenAI key never leaves the server; the browser only gets a client secret that expires after `REALTIME_CLIENT_SECRET_SECONDS` (default 120). A call must start before then.
- Voice credentials are rate-limited to 12 per 5 minutes per professor.
- The session's model, instructions and tools are set by the server when it creates the credential. Even a modified browser can only act through FastAPI's tool endpoints, which validate every call and authorize it as the professor, with Row Level Security.
- Each session carries a `safety_identifier` that is a hash of the professor's id, not the id itself.

## Personalization

`/workspace/preferences` has two parts:

- **Your preferences**: free-text notes the professor writes (up to 2,000 characters), such as "Prefer scenario questions", and a switch for **Learn from my finalized exams and approved revisions**.
- **What ProfPilot has learned**, built only from:
  - exams the professor marks as **final** (`exam_finalized`; finalizing again replaces that exam's signal);
  - questions they **approve** whose latest change was an AI revision (`revision_accepted`, counted once per revision).

  Rejected, deleted or unreviewed AI output never counts. The learned profile is computed in code, not by the model: typical format and difficulty split by marks, typical duration, most used question types, recurring section titles, and tendencies from quick actions accepted at least twice (for example "Values very clear, unambiguous wording (accepted 3 times)"). It can be cleared at any time, and nothing is recorded or used while learning is switched off.

Preferences are passed to the interpreter under "apply gently; the current settings and instructions always override them".

## Final review and export

The **Final review** panel runs the readiness checks (`backend/app/services/readiness.py`):

| Level | Examples | Effect |
| --- | --- | --- |
| Blocked | Generation still running | Export is disabled |
| Serious | An MCQ without a valid answer, a question with no answer key, generation that didn't finish, versions that don't match, no questions | Export only after the professor confirms |
| Warning / info | Unapproved questions, an answer key that may need checking, a split slightly off target, the quality check not run | Shown, never blocking |

**Mark as final** (allowed once export is possible) records the exam as finalized. Finalized exams are what personalization learns from.

`GET /api/exams/{id}/export?kind=student|answer_key|both&version=all|A` produces:

- the **student exam**: course, title, duration, Name and Student ID lines, instructions, sections, questions with marks, choices, figures, and "Page X of Y";
- the **answer key**: the correct choice marked, answers, worked solutions, explanations and rubric tables;
- one PDF, or a ZIP when several files are requested (`midterm-version-A-exam.pdf`, `midterm-version-A-answer-key.pdf`, …).

PDFs are built on the server with ReportLab from the database, as the professor. Student papers and answer keys use the AUB exam style: the bundled burgundy wordmark, a right-aligned course and assessment header between black rules, embedded Computer Modern typography, and `1)` / `a)` numbering. CMPS courses include the Faculty of Arts and Sciences and Department of Computer Science lines; other courses do not assume a department. Marks, versions, student identification lines and page numbers are retained. The bundled fonts and their redistribution licence are in `backend/app/assets/fonts`; no LaTeX installation is needed. Figures come from the private bucket. PDF metadata has no author or producer. Source mathematics remains Unicode; conservative standalone equations are typeset with Matplotlib MathText as vector glyphs with stacked fractions, subscripts and superscripts. Unsupported or overly wide equations stay as wrapped text. Paragraphs and questions have explicit spacing, MCQ choices stay together, and new questions start only when there is room for a useful portion of the stem. Pipe-separated tables and unambiguous legacy numeric tables become native PDF tables with padded cells, weighted column widths and repeating headers. Generation prompts request blank lines around paragraphs and equations and pipe-separated data tables. Code blocks use a monospaced font. For scripts beyond Latin, Greek and common math symbols, install a Unicode TrueType font and set `PDF_FALLBACK_FONT` (for example DejaVu Sans).

## Prompt-injection safety

Uploaded files and professor text are **data**, not instructions:

- Every block of file content, excerpts and professor text is wrapped in a data block marked with a random code generated for that request (`DataFramer` in `backend/app/ai/prompts.py`). The code is stripped from the content, so a file can't fake the end of its block.
- The system instructions state the order of authority (the app's rules, then the professor's settings and instructions, then data) and tell the model to treat any instructions found inside data blocks as content, never as commands.
- Model output is validated against strict schemas and business rules before anything is saved, and tool calls are validated and authorized by FastAPI.

## Background jobs

`ai_runs` records each job's kind (`document_processing`, `interpretation`, `generation`, `review`, `versioning`), stage, status and safe error message. Jobs run inside the FastAPI process:

- the browser polls `GET /api/runs/{id}` every 1.5 seconds and shows the stage;
- a job heartbeats every 30 seconds; a running job without a heartbeat for 3 minutes is marked failed ("This was interrupted before it finished. Please try again.") and can be started again;
- only one job of each kind can run per assessment; a second request returns `409 job_running` and the page attaches to the running one.

## API

All routes except `GET /` and `GET /health` require `Authorization: Bearer <Supabase access token>`. Errors are `{"error": {"code", "message"}}`. Another professor's ids return `404`, as if they didn't exist.

| Method and path | Purpose |
| --- | --- |
| `GET /api/assessments/{id}/ai-status` | Files' reading status, style profile, plan status, exam summary, running jobs |
| `POST /api/assessments/{id}/documents/processing` | Read pending files (`retryFailed`, `documentId`) |
| `POST /api/assessments/{id}/interpretation` | Start the Prompt Interpreter |
| `GET /api/assessments/{id}/interpretation` | The plan, with staleness reasons |
| `PATCH /api/assessments/{id}/interpretation` | Edit the enhanced prompt |
| `POST /api/assessments/{id}/interpretation/approval` | Approve the plan |
| `POST /api/assessments/{id}/exam` | Create the exam (`mode`, `replace`, `resume`) |
| `GET /api/assessments/{id}/exam` | The assessment's exam, if any |
| `GET /api/runs/{id}` | A background job's status |
| `GET /api/exams/{id}` | The full exam |
| `POST /api/exams/{id}/review` | Run the quality check |
| `GET /api/exams/{id}/readiness` | Final review checks |
| `POST /api/exams/{id}/finalization` | Mark as final |
| `GET /api/exams/{id}/export` | PDF or ZIP (`kind`, `version`, `confirm`) |
| `PUT /api/exams/{id}/versions/{versionId}/order` | Reorder questions |
| `PATCH /api/questions/{id}` · `DELETE /api/questions/{id}` | Manual edit · delete |
| `POST /api/questions/{id}/revision` | Ask AI or a quick action (`instruction`, `preset`) |
| `POST /api/questions/{id}/solution` | Regenerate the answer key |
| `PUT /api/questions/{id}/approval` | Approve / unlock |
| `GET /api/questions/{id}/revisions` · `POST …/revisions/{revisionId}/restore` | History · restore |
| `POST /api/questions/{id}/variants` | Update the other versions |
| `GET/POST /api/exams/{id}/builder/messages` | Build with AI text chat |
| `POST /api/exams/{id}/builder/tools/{name}` | Run a builder tool (voice) |
| `POST /api/exams/{id}/builder/transcript` | Save a voice transcript line |
| `POST /api/realtime/client-secrets` | A voice credential |
| `POST /api/setup-assistant/tools/{name}` · `POST /api/setup-assistant/messages` | Set up with AI: voice tool · text chat |
| `GET/PUT /api/preferences` · `DELETE /api/preferences/learned` | Preferences |

The interactive documentation is at http://localhost:8000/docs while the backend runs.

## Cost controls

- Files are read once; identical files are reused by hash; the style profile is cached.
- Retrieval sends only the most relevant excerpts, within a character budget.
- Reasoning effort is set per task; verbosity is kept low where possible.
- Validation failures get one repair attempt, not open-ended retries.
- Concurrency is bounded, and a failed batch cancels its siblings.
- Automated tests never call OpenAI (see below).

## Testing

From `backend/` with the virtual environment active:

```bash
pytest                 # 100 tests, plus 2 live tests that are skipped by default; OpenAI and Supabase are replaced by fakes
ruff check . && ruff format --check .
```

- `tests/fakes.py` has an in-memory Supabase that imitates Row Level Security, constraints, triggers, cascades, RPCs and Storage, and a fake OpenAI service that returns scripted, schema-valid replies and records every call. No test spends API credit.
- `tests/test_isolation.py` acts as a second professor and makes 30 requests against the first professor's assessment, exam, questions, job and voice credentials: every attempt gets `404`, the first professor's data is unchanged, their Storage files can't be read, and requests without a token get `401`.
- `tests/test_security.py` covers token verification (expired, wrong audience, wrong issuer, `anon` role, anonymous users, a non-UUID subject, a token signed by another key, symmetric and unsigned tokens), protected endpoints without a valid bearer token, and error responses that never echo submitted values.
- The other suites cover parsing, ingestion, the interpreter and staleness, generation and resume, the quality check and its safe fixes, revisions and history, the builder and setup tools, voice credentials and their rate limit, personalization, jobs, and export.

**Live smoke test.** `tests/test_live_smoke.py` makes two small real calls (one structured output, one Realtime client secret). It is skipped unless enabled explicitly:

```bash
PROFPILOT_LIVE_TESTS=1 pytest tests/test_live_smoke.py
```

### Manual voice checklist

Automated tests can't use a real microphone. Before a demo, in Chrome or Safari:

1. Open an assessment, choose **Set up with AI**, start a call and allow the microphone.
2. Say "Make it a 90-minute midterm with two versions". The duration and versions change on the form and are highlighted; the change feed lists them.
3. Interrupt the assistant while it speaks; it stops and listens.
4. **Mute**, speak, and check that nothing is heard; unmute.
5. **End call**, reload the page: the changes were saved.
6. Block the microphone in the browser settings and start again: the panel explains and offers text chat.
7. Repeat a short session in **Build with AI**: ask for the next question, ask to make it harder, approve it.

## Limitations

- Jobs run inside the FastAPI process. A restart interrupts running jobs, which then show as interrupted and can be started again. A production deployment would use a job queue.
- Jobs act with the professor's access token, which lasts about an hour. The browser refreshes the session before starting a job, which covers normal generation times.
- Only voice credentials are rate-limited. AI revisions and other endpoints are not.
- Live captions depend on the transcription model. If they don't appear, set `REALTIME_TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe`.
- PDF support for non-Latin scripts depends on the fallback font.

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
| `slides` | Every slide's words, kept page by page; the slide ids the planner cites; the slides shown to writers and the reviewer as images |
| `figure_reading` | Reading the pictures in PowerPoint course material (diagrams, charts, tables, code) into its text |
| `document_retrieval` | Hybrid search over course material: similarity over chunks plus full-text search over every slide's words |
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

Reasoning calls use the **Responses API** with **Structured Outputs** (`responses.parse` with a Pydantic model), so every reply has exactly the expected shape. Each call sets a reasoning effort that suits it, keeping high effort where it materially improves the result: high for planning, the final quality check, and writing, revising or re-keying a question that is hard or a multi-step problem (above easy); medium for other questions, normal revisions, answer keys of such questions, style analysis and versions; low for file summaries, tool routing and chat. The interpreter uses high for complex requests and medium otherwise. Every call uses `store=False`. Tool use (Build with AI, the text chat) uses strict function tools, replaying reasoning items between rounds.

`OpenAIService` (`backend/app/ai/openai_service.py`) wraps every call:

- **Validation and one repair.** A reply that fails the schema or the business rules (percentages that don't add up, an answer key that isn't a choice, duplicate questions) is sent back once with the problems listed. If it is still wrong, the step fails with a clear message; nothing invalid is saved.
- **Truncation, refusals and safety filters** are detected and turned into clear messages.
- **A busy service is waited out.** A rate limit or an overload is retried up to 5 times, pausing 10, 20, 40, 60 and 60 seconds (or as long as OpenAI says), so a long job never fails for a burst of requests. What waiting can't fix fails at once: an OpenAI account out of credit (`ai_quota`) or a single request over the account's per-minute limit (`ai_request_too_large`, with what to do).
- **Errors are translated** into safe messages (`ai_busy`, `ai_timeout`, `ai_unavailable`, `ai_quota`, `ai_request_too_large`, …). Raw OpenAI errors, prompts and keys are never returned or logged. Usage logs record only the purpose, token counts and time.
- **Concurrency is bounded** (`MAX_CONCURRENT_GENERATION_CALLS`, default 3); if one call in a group fails, the others are cancelled.

## Data model

Migration `supabase/migrations/20261005203838_assessment_ai_workflow.sql` adds:

| Table / column | Purpose |
| --- | --- |
| `documents.processing_status`, `processing_error`, `processed_at`, `content_sha256`, `page_count`, `extracted_characters`, `summary` | Whether each file has been read, and what it contains |
| `document_chunks` | Text chunks with `embedding vector(1536)` (HNSW cosine index). Category and assessment are copied from the file by a trigger, so they can't disagree. |
| `match_document_chunks(...)` | Similarity search (`security invoker`, so Row Level Security applies) |
| `document_pages` | One row per page of a lecture PDF (migration `20261028120000_page_understanding.sql`): its words (`text_layer`), `analysed` when it has words or `blank`, the number of pictures the parser found on it (`analysis.pictures`), its words again as searchable text with a full-text index, and the file's SHA-256. A file's pages are deleted with it. (The other columns held an earlier, AI-written reading of each page and are no longer written.) |
| `search_document_pages(...)` | Full-text search over every slide's words, with the same scope as `match_document_chunks` (`security invoker`) |
| `exam_projects.spec_status`, `spec_generated_at`, `spec_approved_at` | The plan's state: `none`, `draft`, `approved`, `stale` |
| `assessment_style_profiles` | The cached previous-exam style profile, keyed by a hash of the files it came from |
| `exam_projects.learning_focus`, `bloom_distribution` | The Exam Learning Focus (migration `20261016120000_bloom_learning_focus.sql`); see [Bloom's taxonomy](#blooms-taxonomy-exam-learning-focus) |
| `exams` | One per assessment: mode, status, the approved spec it was built from, the plan, the quality-check result, `finalized_at` |
| `exam_versions`, `exam_sections`, `exam_questions` | The exam. Equivalent questions in different versions share a `slot_id`. `exam_questions.cognitive_level` is the Bloom's level a question assesses (`null` for questions written before levels were recorded). |
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
2. **Reuse.** If the professor already processed a file with the same SHA-256 hash, its chunks, summary and slides' words are copied: no new AI cost.
3. **Extract text.** PDF page by page (pypdf), PPTX slides, tables and speaker notes, DOCX headings and tables, TXT in common encodings. Damaged, password-protected and empty files fail with a specific message.
4. **Scanned PDFs** (at least half the pages without a text layer): those pages are transcribed by the reasoning model, eight pages per request, up to `MAX_OCR_PAGES` (default 30) pages.
5. **Images** are described by the reasoning model (what they show, any text, labels and data).
6. **A lecture PDF's slides are kept as words, the way a chat assistant takes a PDF** (`slides.py`). No AI reads or describes the slides when the file is read: every page's words come from the file itself (exact, at once and at no cost; a scanned page's from its transcription) and are kept page by page in `document_pages`, with the number of pictures on each page. The PDF stays in Storage, and the slides themselves are shown to the AI as the exam is written (see **The slides, as the exam is written** below), so its diagrams, charts, tables and formulas are seen exactly as drawn.

   **PowerPoint pictures.** A PowerPoint file can't be rendered on the server, so its slides can't be shown later: they keep their text, tables and notes, and their pictures are read now instead. The parser keeps the pictures that may carry content: at least 5% of the slide, a long strip such as an equation or a line of code (from 1%), or the main thing on a slide with almost no text. It skips icons, emoji and logos (smaller than that), the slide template (a picture on at least half the slides), a picture shown again on later slides (read once, where it first appears), and the title and closing slides ("Thank you", "Questions?"). The reasoning model is shown each such slide's pictures with its text, eight slides per request, up to `MAX_FIGURE_PAGES` (default 60) per pass, and writes down what each picture shows: a diagram's boxes and arrows with their labels, a chart's axes and values, a table's rows, code line by line, equations in LaTeX. The reading goes into the slide's text between `[Figure on page N, read by ProfPilot: kind]` and `[End of figure]`; decoration is noted as `decorative only`. A slide that couldn't be read keeps its "not in the extracted text" marker, and the file is read anyway. A chart's own data (categories and values) is copied as text, at no cost. Previous exams and attachments aren't read either way.
7. **Chunk** (about 2,400 characters with 300 overlapping, labelled "Page 3", "Slides 1–3" or "Section: …", never across a page without text, so a label never claims a missing page), **embed** in batches, and store. The file is now searchable ("ready").
8. **Course material** then gets its **Lecture Knowledge Profile** (below), which also gives its title and summary. Other files get a short **summary** (title, summary, topics with locations).

### Lecture knowledge

Each course-material document gets one reusable, structured profile (`documents.knowledge`, built by `lecture_knowledge.py`). The chunks stay the authoritative evidence; the profile is a map of everything the lecture contains:

- **Complete coverage.** The stored chunks are analysed in order, in sections of up to 24,000 characters, one call per section, so long lectures are never truncated or sampled. Every item (concepts and definitions, formulas with variables and conditions, examples and applications, figures and tables, misconceptions, and the Bloom's levels each topic can support) cites the locations its section really contains; a reply citing any other location is sent back once. Each page or slide starts with a marker in the stored text (`[Page 14]`), so items cite the exact page even inside a multi-page chunk. Misconceptions are student errors and say whether the lecture states them or ProfPilot suggests them; weaknesses of a method are concepts of kind `limitation`. Create opportunities need real grounding (a design or build task the material supports).
- **Formula fidelity.** Formulas are kept as written (`expression`, the evidence) and flagged `uncertain` when the extracted text looks garbled. A formula still written in a PDF's mathematical letters (`𝒛=𝒘.𝒙+𝒃`) is always unverified, whatever the analysis said (`is_unverified`, applied to saved profiles too). A proposed standard form goes in a separate `normalized` field, only when it's unambiguous; it never replaces the evidence and is shown as ProfPilot's reading, not the lecture's. Question writers see an unverified formula labelled as such, with its standard form (if any) apart from the extracted evidence, and are told never to quote or reconstruct it; the planner avoids questions that depend on one. Every AI-written question (generation, versions, single questions, revisions) is rejected and sent back if it contains characters copied from extracted PDF maths (`𝒇`, `𝒆`…) or quotes an unverified formula's text. The quality check warns on any question with mathematics planned on a topic with unverified formulas, asking the professor to confirm it against the original pages. The only confirmation it accepts is the professor's own text: a question is not flagged when every formula it states as correct (prompt, key, answers, solution, rubric; never its distractors) matches one the professor wrote in this assessment's instructions or notes, and it states at least one (`traced_to_professor`). Statements that forbid or correct something ("Do not use …") confirm nothing, and calculations with numbers can't be traced to a formula, so questions doing them keep the warning. Notation must match after normalisation (`w·x` = `wx`, but `f(x)` ≠ `f`).
- **Relations.** One short call over the merged topic inventory gives the title, overview, section structure, learning objectives and the relations between topics (prerequisite, builds on, contrasts with, trade-off…); relations naming unknown topics are dropped.
- **Structure and Bloom's levels.** Worked examples, applications, code demonstrations and datasets go in `examples` (with their details), ideas in `concepts`. Evaluate is offered when the material gives grounds for a judgement (trade-offs, choosing or justifying a method or metric, critiquing a flawed derivation, result or code); no level is added just to cover it. Remarks about the answer format are dropped from warnings (`material_warnings`, also when showing saved profiles).
- **Coverage record.** Every page or slide is classified: substantive text, text with unread figures, figure-dominated (only titles, image credits, URLs or axis labels besides the figure), title or divider (headers, footers and lines repeated on half the pages don't count as content), empty, plus the pages whose maths was garbled. Only substantive pages count as analysed content; a PowerPoint slide whose pictures ProfPilot read counts as substantive (`units_with_read_figures`). The parser marks figures beyond a slide template's own images (PDF) and pictures or charts (PowerPoint). The record also says whether the file was truncated and how many tokens the build used. Files read before page markers existed only show which pages had any text, and say so (`page_detail: false`); re-reading the file gives the full detail.
- **Once per version.** A profile is current while its version (`KNOWLEDGE_VERSION`) and the file's SHA-256 match; an identical file of the same professor reuses it: copied when the file is read, borrowed by assessments when the copy has none, and copied instead of analysed when a build is requested. Two identical copies are never analysed at once. Another professor's profile is never used. Every course-material file is left prepared when it's read, a copy of an earlier file included (it reuses what that file has and gets the rest): a lecture PDF's slides' words kept (a PowerPoint file's pictures read, a long lecture's in a few passes), then its profile built. Course sources read before that are brought up to date without the professor asking (see **Preparing course sources** below); `POST /api/documents/{id}/knowledge` builds one at once (the Retry after a failure).
- **Refreshing a legacy lecture.** A profile built before page markers is marked `legacy` (it works, without page-level coverage); the stored text's own state is `legacy`, `current`, `interrupted` or `not_applicable` (Word and text files). Refreshing is two steps, both run by the preparation below:
  1. **Re-read** (`POST /api/documents/{id}/extraction`, PDF and PowerPoint): the original file is parsed again with the current parser. No language-model call is made: only the new chunks are embedded (a fraction of a cent). The document id, file, digest, course, assessment links, summary and profile are kept. The new chunks are saved (at higher indexes) before the old ones are removed, so search never loses the file; a failure removes what was added, and an interrupted re-read (both sets present) is reported, never built on, and cleaned up by the next re-read. A file that needed AI transcription, or whose stored bytes no longer match its digest, is refused without any change.
  2. **Rebuild**: the working profile stays in place and in use until the new one is saved; a failed or interrupted rebuild is recorded beside it (`rebuild`), never replacing it. An identical copy's profile is copied instead only if it has page-level detail.

  A file can't be re-read while it is being read or analysed, or analysed while it is being re-read.
- **Preparing course sources** (`DocumentIngestionService.prepare`, with the rules in `source_preparation.py`). No professor has to prepare files one by one. Opening Sources calls `POST /api/sources/preparation`, which looks at every course source and, in the background, two at a time, brings up to date each one that isn't. A lecture PDF has its slides' words kept (at no cost) and, if its text predates page markers or a re-read was interrupted, is re-read (free); a PowerPoint file whose text predates page markers, whose re-read was interrupted or whose pictures were never read is re-read with its pictures read (several passes for a long lecture, `MAX_FIGURE_PAGES` slides each). Then its lecture knowledge is built, or rebuilt when it was missing, outdated, legacy, failed, or older than the pictures read. Readings already made are kept and never paid for twice. A file is prepared when its profile is current and was built from text with page detail (a PowerPoint file: with every picture read); that's judged from the profile alone, so checking prepared files costs nothing. Each lecture's panel shows what's under way (`preparation`: queued or running; "Building lecture knowledge…") and checks back until it's done. A file whose preparation failed isn't tried again on its own until the server restarts (so a file that always fails never keeps spending), and its panel offers **Retry**; a run starts no new file after 40 minutes, so it never outlives its session (the rest wait for the next visit). Files being read, analysed or re-read are skipped. `POST /api/documents/{id}/figures` reads a PowerPoint lecture's pictures again at once (**Try again** after a failed reading).
- **No assessment settings.** The profile lists the levels the material supports; each assessment's own Bloom's split stays in its ExamSpec.

Planning uses it: the interpreter and planner see a compact map of every lecture: every topic with its id (`L2.t5`), merged locations ("Slides 3–5"), supported levels as initials, and formula, example and unreadable-figure counts; relations and warnings beyond a few are counted, not listed. No topic is ever left out (about 5,000 tokens for six 30-topic lectures), and the file overview doesn't repeat what the map says. When every lecture is mapped, the planner samples one excerpt per lecture instead of three. Each planned question cites the topics it draws on (`knowledge_refs`, checked against the inventory), and its question writer receives exactly those topics' full notes alongside the retrieved excerpts, which remain the authority. Lectures without a current profile are named, and work as before (overview and excerpts).

#### The slides, as the exam is written

Each request carries what it needs, the way a PDF goes with a question to a chat assistant (`slides.py`); nothing about a request is stored:

- **The plan writer (the Prompt Interpreter) and the planner get every word of every slide** of the assessment's lecture PDFs (`ALL SLIDES`), each slide under a short id (`F1:p8`, lecture 1, page 8) and marked when it shows pictures its words don't describe (its writer will see the slide itself). Even a dozen lectures fit; beyond about 320,000 characters, long slides are shortened evenly and the planner is told.
- **Every question traced to its slides.** Each planned question cites the slides it is built on (`source_pages`), checked in code: a slide that isn't listed, or a question citing none, is sent back for repair. A lecture of at least 10 content slides whose questions all come from its first 40% is sent back once, to spread them over the whole lecture (a warning if it stays).
- **Each writer sees its questions' slides.** For every batch of questions, the stored PDFs are opened (each downloaded once per exam) and the questions' slides rendered as images (about 144 dpi, at most 2,048 pixels a side; up to 12 per request), sent with every word on them (`THE SLIDES OF THESE QUESTIONS`) and the retrieved excerpts. The writer is told the image is the authority on diagrams, charts, tables, code and formulas, and that a question built on one states what students need, since students never see the slides. A slide that can't be shown still goes by its words.
- **The reviewer sees the same slides** (images and words, up to 16; `SOURCE SLIDES OF EACH QUESTION`) and checks every answer key against them.
- **Grounded answers.** A question keeps its slides as its first sources ("Based on: Lecture 3.pdf, Page 8"), ahead of its lecture topics' pages; they survive revisions.
- **Older lectures.** A lecture read before its slides' words were kept gets them when an exam is written (from its PDF, no AI).
- **No exam from a lecture still being read.** `POST /api/assessments/{id}/exam` is refused (`409 material_not_ready`) while a lecture PDF is waiting to be read or being read; the refusal names it and starts reading it, which takes seconds.

The three categories stay separate:

- **Course material** is the only source searched for question content.
- **Previous assessments** are used only for the style profile, never copied into new questions.
- **Attachments** are described to the interpreter and planner, and an image can be attached to a question as its figure.

### Retrieval

`DocumentRetrievalService.search` is hybrid. It embeds one or more queries (topics, concepts, the professor's request) and calls `match_document_chunks` for course material only (meaning), and, for each query, `search_document_pages` (words: Postgres full-text search over every slide's words, any of the query's words of four letters or more). The two lists are merged by reciprocal rank (k = 60): a slide found by its words that's already inside a chosen chunk lifts that chunk instead of repeating it; otherwise its words are added as an excerpt of their own ("Page 8"). The result is fitted into a character budget. If the word search is unavailable, meaning search is used alone. Excerpts are labelled `S1`, `S2`, … so questions can cite them (`source_excerpt_ids`). A citation is kept only if it matches an excerpt that was actually provided, and is shown under the question as "Based on: Lecture 3.pdf, Slides 4–6".

## Previous-exam style analysis

When previous assessments are uploaded, `style_analysis` builds a **style profile**: common question types, typical length, difficulty, conceptual vs computational, recall vs application, wording, scenarios, sub-questions, section structure, grading patterns, recurring preferences with evidence, and limitations. It is cached by a hash of the files and their contents and only rebuilt when they change. The form notes it under **Previous assessments** ("ProfPilot analysed 2 previous exams: …"), and so does the overview's **AI assessment** card.

The profile guides style only. The professor's settings and instructions always win, and questions are never copied from a previous exam.

## The Prompt Interpreter

**Improve with AI** saves the form, then starts an interpretation job. The interpreter reads:

- the setup (course, name, duration, format, difficulty, versions, notes, instructions);
- summaries of every file and the most relevant course-material excerpts;
- every word of every slide of the lecture PDFs (`ALL SLIDES`), the authority on what each lecture contains, with the rule that a slide's pictures are shown to the question writers as images, so they're never called missing; a lecture whose knowledge isn't built yet (or failed) has it built first;
- the style profile;
- the professor's preferences.

It returns an **enhanced prompt** (a clear, complete brief the professor can edit) and an **ExamSpec** (`backend/app/schemas/ai.py`):

| Group | Fields |
| --- | --- |
| Identity | `assessment_title`, `course`, `assessment_type` |
| Format | `duration_minutes`, `versions`, `total_points`, `question_count_min/max`, `question_format` (MCQ / subjective %), `difficulty` (easy / medium / hard %), `bloom` (learning focus and % per Bloom's level), `sections` |
| Content | `coverage` (topics with emphasis and source files), `learning_emphasis`, `question_style`, `professor_notes`, `professor_preferences_applied`, `source_material_guidance`, `previous_exam_style_guidance`, `constraints` |
| Transparency | `assumptions`, `warnings`, `generation_instructions` |

Every setting carries its **provenance**: `professor` ("Your setting"), `ai_inferred` ("Inferred" from the material or instructions) or `ai_assumption` ("Assumption", where generation needed a value nobody gave). Values the professor set in the form are re-applied in code after the model replies, so the AI can never override them; it can only fill gaps and flag conflicts in `warnings`.

### Plan review and approval

The plan page (`[id]/plan`) shows the spec with provenance badges, the assumptions and warnings, and the enhanced prompt.

- **Edit** the enhanced prompt; the plan goes back to **draft**.
- **Regenerate** the plan.
- **Approve & continue**; generation only starts from an approved plan.

A plan becomes **out of date** when the setup changes, a file is added or removed, or a file that failed to read is now ready. The page then explains what changed and asks for a new plan; an out-of-date plan can't be approved. The staleness check runs both in the database (triggers) and in the API (`stale_reasons`).

## Bloom's taxonomy (Exam Learning Focus)

The professor chooses how the exam's **marks** are spread across Bloom's six levels: remember, understand, apply, analyze, evaluate, create. It is independent of difficulty, and both are respected (an analyze question can be medium; an apply question can be hard).

| Focus | Stored | In the ExamSpec (`bloom`) |
| --- | --- | --- |
| Automatic (default) | `learning_focus = 'automatic'` | Recommended by the Prompt Interpreter from the material, objectives, formats and assessment type, in the same call (`source: ai_inferred`, `focus: automatic`) |
| Balanced, Practical, Critical Thinking | the choice only | The preset's split, re-applied in code (`source: professor`, `focus` names the preset) |
| Custom | `learning_focus = 'custom'` and `bloom_distribution` | The professor's six percentages, exactly (`source: professor`) |

- **Feasibility is checked in code, not left to the model.** If more marks are at Create than the subjective share allows (multiple-choice questions can't assess creating), a warning on the plan page asks the professor to adjust; nothing is changed silently (`distribution.bloom_conflicts`). A plan with a multiple-choice question at Create is rejected and repaired.
- **Planning.** Every planned question has a `cognitive_level`. The split of marks per level is compared with `spec.bloom` like the format and difficulty splits (within 6 percentage points), with the same single repair call. Plans saved earlier with the old labels (`recall`, `application`, …) still load.
- **Writing.** Each batch is told every question's planned level, with one shared guide to what each level requires (`BLOOM_GUIDE` in `prompts.py`): a level comes from the reasoning a student must do, never from the verb in the stem. A question whose `cognitive_level` doesn't match its plan is sent back. Equivalent versions must keep the level. In Build with AI, the next level is the one furthest behind the split (`suggest_level`), or the one the professor asks for.
- **Review.** The quality check judges whether each question really needs its labelled level. If not, it may only **relabel** it to the level it actually assesses (a safe fix, in the revision history); rewriting it to reach another level is left to the professor. The split per version is checked deterministically too, and versions whose equivalent questions differ in level are reported.
- **Editing.** Questions show their level. AI revisions set the level of the revised question; quick actions that keep what a question tests (clearer, shorter, alternative, replace) must keep it. The professor can change it in **Edit manually**.
- **Where it shows.** The setup form (cards, an advanced panel with six sliders and a doughnut chart), the summary, the plan page (with an **Adjust** link back to the form), question cards and the exam's **At a glance** panel.

## Generating the exam

### Generate full exam

`POST /api/assessments/{id}/exam` with `mode: "full"` starts a job with visible stages:

1. **Planning.** Only once every lecture PDF is read (see **The slides, as the exam is written** above). The reasoning model writes an exam plan from every word of every slide: sections, and for each question its type, difficulty, points, topic, learning objective, cognitive level, source hint, source slides (`F1:p8`) and estimated time. The plan is checked in code (counts, total points, format and difficulty split by marks within 6 percentage points, duration) and repaired once if needed.
2. **Writing questions and answer keys.** Questions are written in batches of up to four, enough batches to keep `MAX_CONCURRENT_GENERATION_CALLS` busy in as few rounds as possible, each shown its questions' slides (as images, with their words) and grounded in retrieved course-material excerpts. Questions that need high effort are batched apart from the others and go first, and the batches are shared out so the slowest one finishes as early as possible; every question keeps its planned number, so the order never depends on timing. Each batch is validated before it is saved: the planned type, difficulty and points; a valid answer for every MCQ; an answer, a worked solution and a rubric whose points add up; no near-duplicates of other questions.
3. **Creating equivalent versions** (if more than one). Each question of version A gets an equivalent variant per version, with the same topic, type, difficulty and points; equivalents share a `slot_id`. All versions are written at the same time.
4. **Running the AI quality check** (below).
5. **Finalizing.**

The plan is made once, and the full quality check runs once, after every question is written.

**Sources and question hygiene.** Excerpt ids (`S1`, `S2`…) are numbered per request, so the brief and plan cite files, pages and lecture topic ids instead. A question may cite only the excerpts its own request gave; any other id is sent back for repair, never silently dropped. Each planned question also keeps the exact pages of its lecture topics (`source_refs` entries with a `topic`), which the answer key shows ahead of the excerpts' page ranges and which survive revisions. **Integrity across questions.** No question may give away another's answer: an MCQ's correct answer stated in another question's student-facing text (verbatim, as the same mathematics in other notation — LaTeX and plain forms are compared in one canonical form — or as a close paraphrase of its distinctive words) is sent back for repair within a generation batch, in Build with AI and in revisions, and reported (`answer_leakage`) by the quality check, which runs it after the reviewer's safe fixes and tells the reviewer what it found so a minimal rewording can remove it. Shared formulas no question asks for, text that matches a distractor too, and short answers are not leaks. The correct formula with its variable renamed or given a value counts as the formula, unless the result fits a distractor as well. **Partial leaks** are checked the same way (`partial_leaks`): a result another question asks students to work out (a decimal of at least 3 significant digits from its key, not given in its own text) printed elsewhere — in a lookup table, as given data or in a worked step — and the reasoning a written question's model answer gives, supplied by another question's text (most of a sentence's distinctive words). Data both questions are given, integers and short decimals are never leaks; subtler cues (a sign in an exponent, an interpretive phrase) are left to the reviewer, whose instructions cover them. **Reviewer fixes to parts.** The reviewer may reword one subpart's prompt or replace one subpart's rubric (`subpart_label`); the part's answer, rubric (for a rewording), points, the question's level and sources stay as they are. Every fix is validated before anything is saved: no new problem in the question, no new give-away anywhere in the version, and a rewording keeps the mathematics students read — nothing added or changed; removing a formula or value only when that removes a give-away and the answer key doesn't use it (a lone digit, such as a class label, may go). A rejected fix changes nothing and records no revision. An accepted fix is saved with its revision by `ExamStore.change_question`: the change applies only if the question is still as the review read it (a professor's edit or approval meanwhile wins), and since the API runs each write in its own transaction, a revision whose change failed is deleted again, while a change saved before its request failed keeps its revision. Only if that cleanup fails too does a revision remain, recording the question as it still is (restoring it changes nothing). Fully atomic saving would need a database function (a migration). **Distinct targets.** The plan gives each question an `assessment_target` (the task and case), shown to every writer; two targets that are the same task with only numbers changed are sent back once. A concept may return progressively, with different thinking on a different case. The planner and writers keep points proportional to the work (no trivial inputs for many marks; edge cases earn the marks of the reasoning they need) and the reviewer reports mismatches (`marks`). Demanding pairs (Remember at medium or hard, Understand at hard) get guidance in every step and an informational note in the quality check; the professor's setting is never changed. Marks written into question text ("(2 marks)") and internal ids in concepts are rejected too.

Every AI-written question is rejected and repaired if its text names internal ids ("Source: …; S4", "L1.t3") or if its main prompt restates its subparts (students would see each task twice). Writers are told that a hard question is hard because of the thinking it needs, never because the stem lists what the answer must contain; that no fact, case or calculation is assessed twice; and that every distractor is a plausible misconception of the same kind as the answer.

#### Question order

Unless the professor asks for a particular order, an exam mixes difficulties and topics instead of following the course outline: not all the easy questions first, and not several questions in a row from the same lecture while others are available. The planner is told so, and the plan is then checked in code (`interleave_plan` in `exam_generation.py`): where every difficulty, or every lecture (from each question's source), forms one block, or a run of three or more could be broken up, the questions of that section are reordered so they spread evenly. Only the order changes (each question keeps its type, difficulty, points and topic, so the splits by marks stay exactly as planned), sections stay together, a section grouped by question type keeps that grouping, and questions are renumbered. No extra model call is needed. Small sections are left alone.

An explicit request wins: when the professor asks for an order ("easy questions first", "lecture by lecture", "finish Lecture 3 first"), the planner reports it in `ordering_request` and the plan is kept as it is; the professor's own words are also checked for such requests.

If a step fails, the questions already saved are kept and the exam shows **Continue generation**, which resumes where it stopped (`resume: true`). Replacing an existing exam requires an explicit confirmation (`replace: true`).

### Build with AI

`mode: "interactive"` creates an empty exam and opens `[id]/builder`: the exam on one side, a conversation with ProfPilot on the other (tabs on a phone). The professor can type or talk. Both use the same tools, executed by FastAPI:

`get_exam_state`, `generate_next_question`, `revise_question`, `approve_question`, `unlock_question`, `delete_question`, `move_question`, `review_question`, `regenerate_solution`, `undo_question_change`, `finish_exam`.

The assistant follows the approved plan, suggests the next question, and never changes an approved (locked) question. Its view of the exam lists the course material in use (direct uploads and the selected course sources) and the split by Bloom's level reached so far. **Undo** restores the version before a question's most recent change, through the same history as the editor's **Restore**. It goes back one step: because a restore is itself recorded, a second undo reverses the first, and the reply says which happened. **Finish** creates the other versions and runs the quality check, then opens the exam editor.

- **The next question keeps the mix.** Unless the professor asks for a type, difficulty or topic, ProfPilot picks them (`suggest_next`): whichever difficulty is furthest behind the plan's split by marks, without repeating the previous one while another is about as far behind, and the coverage topic furthest behind its share, avoiding the previous topic and lecture. So difficulties and lectures are interleaved from the start rather than all the easy questions first. "Give me three easy questions first" or "finish Lecture 3 first" is followed as asked.
- **Only what one question needs.** Generating or revising a question loads the stored plan, the exam and a few excerpts (in parallel), makes one reasoning call, and changes only that question and its answer key: no file is read again, nothing is re-planned, and the full quality check doesn't run.
- **Visible progress.** As soon as a tool starts, the conversation shows what is happening ("Generating the next question…", "Making Question 4 harder…", "Updating the answer key…"), as a status, not as a message from ProfPilot. A failure is replaced by a short error and **Retry**. In the text chat the reply streams (`Accept: application/x-ndjson`): a `tool` line as each tool starts, then `done` with the reply. In a call, the tool's result goes back to the model at once and the preview refreshes in the background; the assistant says at most a few words before a long tool and then waits quietly.

## The quality check

`ExamReviewService.review` combines:

- **Deterministic checks:** structure, answer keys, edited questions whose key may be out of date, near-duplicates, the format and difficulty split per version, and whether the versions match.
- **An AI review** (high reasoning effort) of every question against the course excerpts: answer-key errors, ambiguity, missing information, impossible or unanswerable questions, difficulty, clarity, rubric and solution problems, duration.

The reviewer sees each question's full marking scheme (subpart rubrics included) and the sources ProfPilot attached, so it doesn't report missing rubrics or references that exist. While the check runs, the exam page says so; approving a question stays possible, and the page explains that an approved question is locked, so the check can report its issues but not fix them.

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

**Set up with AI** fills the form live. Each change is highlighted, listed in a change feed, and saved to the same draft as the form (an autosave). It is always offered at the top of Assessment Setup; the sidebar's **AI Assistant** opens the same page with it open.

- **Brief, one question at a time.** The assistant asks about one setting per turn (course, assessment type, duration, question format, difficulty mix, learning focus, versions, coverage), in a few words, without filler or repeating back what the professor said. Every tool result names the next question (`nextQuestion`), skipping settings already filled in or answered.
- **Everything optional.** "Skip", "I don't care" or "not now" calls `skip_setting`: the setting stays empty and isn't asked about again in that conversation. "That's enough" or "finish" ends the setup right away.
- **Exact numbers.** Values the professor states are applied exactly: "30 percent easy, 40 percent medium and 30 percent hard" sets 30 / 40 / 30. A split that doesn't add up to 100 (30 / 30 / 30) is never adjusted; nothing changes and the assistant asks one short question ("That adds up to 90%. What should the remaining 10% be?"). A missing share is asked for, and without numbers ("mostly medium") a split is only offered as a suggestion and set once the professor agrees. Numbers sent as `"30"`, `30.0` or `0.3` are read as the same 30%.
- **Learning focus (Bloom's Taxonomy).** `set_learning_focus` sets Automatic, Balanced, Practical & Problem-Solving, Critical Thinking, or a custom split of all six levels, with the same exact-number rules as difficulty. It is asked about once, after the difficulty mix, and only when the form still has the default (Automatic); skipping it keeps the professor's current choice.
- **Course sources.** `list_course_sources` lists the chosen course's ready sources from the database, and `set_course_sources` adds, removes or replaces the selection using only those ids. The form sends its current selection with each request; the result's `sourceIds` (with `sourceIds` in `changedFields`) updates the Course material card, and saving goes through the same `setAssessmentSourceDocuments` as a manual selection. Changing the course clears the selection, as on the form.

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

PDFs are built on the server with ReportLab from the database, as the professor. Student papers and answer keys use the AUB exam style: the bundled burgundy wordmark, a right-aligned course and assessment header between black rules, embedded Computer Modern typography, and `1)` / `a)` numbering. CMPS courses include the Faculty of Arts and Sciences and Department of Computer Science lines; other courses do not assume a department. Marks, versions, student identification lines and page numbers are retained. The bundled fonts and their redistribution licence are in `backend/app/assets/fonts`; no LaTeX installation is needed. Figures come from the private bucket. PDF metadata has no author or producer. The exam editor renders LaTeX with KaTeX, using inline \( ... \) or $ ... $ and display \[ ... \] or $$ ... $$ delimiters. New generation requests standard LaTeX math. The PDF typesets the supported MathText subset as vector display equations and print-resolution inline math, with stacked fractions, subscripts, roots and summations. Existing Unicode and plain-text equations remain supported. Unsupported or overly wide equations stay as wrapped text. Paragraphs and questions have explicit spacing, a question that fits on a page stays together with its table and choices; otherwise it moves to the next page. Only content taller than a full page may split. Tables that fit a page also move intact, including within oversized questions. Pipe-separated tables and unambiguous legacy numeric tables become native PDF tables with padded cells, weighted column widths and repeating headers. The editor renders these tables as semantic HTML tables with horizontal scrolling. Generation prompts request blank lines around paragraphs and equations and pipe-separated data tables. Unsupported math remains visible as source for review, rather than disappearing. Code blocks use a monospaced font. For scripts beyond Latin, Greek and common math symbols, install a Unicode TrueType font and set `PDF_FALLBACK_FONT` (for example DejaVu Sans).

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
| `GET /api/documents/{id}/status` · `POST …/processing` | A file's reading status · read one course source |
| `GET /api/documents/{id}/knowledge` · `POST …/knowledge` | A lecture's knowledge, stored text and pictures state · build or rebuild its profile |
| `POST /api/documents/{id}/extraction` | Re-read a lecture's text with the current parser (free; pictures already read are kept) |
| `POST /api/documents/{id}/figures` | Read a PowerPoint lecture's pictures again, in the background (after a failed reading) |
| `POST /api/sources/preparation` | Bring the professor's course sources up to date (slides' words kept, a PowerPoint's pictures read, lecture knowledge built), in the background |
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
| `GET/POST /api/exams/{id}/builder/messages` | Build with AI text chat (streams progress with `Accept: application/x-ndjson`) |
| `POST /api/exams/{id}/builder/tools/{name}` | Run a builder tool (voice) |
| `POST /api/exams/{id}/builder/transcript` | Save a voice transcript line |
| `POST /api/realtime/client-secrets` | A voice credential |
| `POST /api/setup-assistant/tools/{name}` · `POST /api/setup-assistant/messages` | Set up with AI: voice tool · text chat |
| `GET/PUT /api/preferences` · `DELETE /api/preferences/learned` | Preferences |

The interactive documentation is at http://localhost:8000/docs while the backend runs.

## Cost controls

- Files are read once; identical files are reused by hash; the style profile is cached.
- A lecture's knowledge profile is built once per file version (its summary comes with it, not as a separate call) and reused by every assessment; it's rebuilt only when it's outdated, legacy, failed or older than the lecture's pictures. Each build records its token usage on the profile and logs a `lecture_knowledge` timing line.
- Reading a lecture PDF costs no model call: its slides' words come from the file. The slides are seen where they're used: each writing request carries its questions' slides as images (up to 12, about 1,500 tokens each) and the review the exam's (up to 16), rendered from the stored PDF, which is downloaded once per exam.
- A PowerPoint lecture's pictures are read once per slide, only where they may carry content (no icons, logos, slide template, repeated pictures, title or closing slides), at most `MAX_FIGURE_PAGES` slides per go; readings are kept when the file is re-read. Preparing course sources skips a prepared file without loading its text, and never retries a failed one on its own until a restart.
- An OpenAI account out of credit is reported as such (`ai_quota`), not as "busy".
- Retrieval sends only the most relevant excerpts, within a character budget.
- Reasoning effort is set per task (see [Models](#models)); verbosity is kept low where possible.
- Independent reads (the assessment, its files, the style profile, preferences; the exam's parts; one similarity search per query) run in parallel.
- Timing logs (`profpilot.timing`) record, per operation, only its name, elapsed time, and the number of model calls (with their purpose), embedding requests and searches, for example `builder_tool.generate_next_question: 8412 ms, 1 model call(s) [generate_question], 1 embedding call(s), 1 retrieval(s)`. No prompts or content.
- Validation failures get one repair attempt, not open-ended retries.
- Concurrency is bounded, and a failed batch cancels its siblings.
- Automated tests never call OpenAI (see below).

## Testing

From `backend/` with the virtual environment active:

```bash
pytest                 # 397 tests, plus 2 live tests that are skipped by default; OpenAI and Supabase are replaced by fakes
ruff check . && ruff format --check .
```

- `tests/fakes.py` has an in-memory Supabase that imitates Row Level Security, constraints, triggers, cascades, RPCs and Storage, and a fake OpenAI service that returns scripted, schema-valid replies and records every call. No test spends API credit.
- `tests/test_isolation.py` acts as a second professor and makes 30 requests against the first professor's assessment, exam, questions, job and voice credentials: every attempt gets `404`, the first professor's data is unchanged, their Storage files can't be read, and requests without a token get `401`.
- `tests/test_security.py` covers token verification (expired, wrong audience, wrong issuer, `anon` role, anonymous users, a non-UUID subject, a token signed by another key, symmetric and unsigned tokens), protected endpoints without a valid bearer token, and error responses that never echo submitted values.
- The other suites cover parsing, ingestion, the interpreter and staleness, generation and resume, the quality check and its safe fixes, revisions and history, the builder and setup tools, voice credentials and their rate limit, personalization, jobs, and export.
- `tests/test_bloom.py` covers the learning focus: its rules, older records, provenance (custom, presets, automatic), conflicts, staleness, the split by marks, plans, generation, versions, Build with AI and editing.
- `tests/test_slides.py` covers using a lecture PDF the way a chat assistant does: every slide's words kept as it's read with no AI reading (a scanned lecture's transcribed), pictures counted for the planner, an identical file's slides copied, files read a few at a time, the planner given every word of every slide, each writer shown exactly its questions' slides as images with their words, the reviewer shown the same slides, questions citing their slides, older lectures given their slides' words when an exam is written, a slide that can't be shown going by its words, plans that must cite real slides spread over the lecture, search finding a slide by its words, and no exam while a lecture is still being read. The fake model records exactly what each request showed it, so these tests check what reaches the model, not how well a real model reads a slide.
- `tests/test_openai_busy.py` covers waiting out a busy OpenAI service (OpenAI's own wait, then growing pauses; reported as busy only after the last), and failing at once with a clear message for an exhausted balance, a request over the account's per-minute limit, or an invalid request.
- `tests/test_source_preparation.py` covers preparing course sources with no one asking: an older lecture's slides' words kept and its knowledge built once (and nothing done on the next visit), a lecture with no knowledge, one read before page markers, failures not retried on their own (and Retry), only the professor's own idle course sources, and a just-read source shown as being analysed.
- `tests/test_figure_reading.py` covers which PowerPoint pictures are read (not icons, logos, the slide template, repeated pictures, title or closing slides), what goes into the text, decoration, failures that keep the marker, forged markers, charts, keeping readings when a file is read again, ownership, and that no structured-output format sends a schema `default` (strict mode rejects one).
- `tests/test_planning.py` covers question order (mixed plans, explicit orders kept, Build with AI's next question) and `tests/test_latency.py` checks that one-question operations make one model call and no heavy work, that batches and versions are written concurrently, and that the chat reports a tool before the AI returns.

The frontend has component tests (from `frontend/`: `npm test`), run with Vitest in Chromium: Set up with AI on Create assessment and through the sidebar, the Exam Learning Focus (choices, sliders and boxes, validation, save and reopen, the plan page, phone width), the sidebar's links, the conversation log's scrolling, the builder's statuses, the builder page's height at 390–1440 px, that captions during a call don't re-render the form or the exam preview, and how a lecture PDF's pictures are described in its coverage.

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
- Bloom's levels are recorded per question, not per subpart (a question's level is the highest it needs). Set up with AI doesn't set the learning focus by voice or chat yet, the PDFs don't print levels, and personalization doesn't learn a cognitive mix. Adjusting the split on the plan page means editing the setup, which asks for a new plan.
- Live captions depend on the transcription model. If they don't appear, set `REALTIME_TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe`.
- PDF support for non-Latin scripts depends on the fallback font.
- The planner sees each slide's words, and which slides show pictures, not the pictures themselves: what a diagram or chart shows is seen by the writer of a question built on that slide (and by the reviewer). A slide that is only a picture may be planned on a little less.
- How well a slide is understood depends on the model. The automated tests use a fake model, so they prove that every slide's words reach the planner and each question's slides reach its writer and the reviewer, not how well a real model reads a given slide.
- PowerPoint files aren't rendered as images (that would need an office renderer on the server): their text, tables, notes, chart data and pictures are read into their text instead. Exporting a deck to PDF has its slides shown to the AI.

# Assessment setup

How a professor sets up an assessment in ProfPilot AI, how drafts and uploaded files are stored, and how each professor's files stay private.

> **Scope.** Assessment setup collects and saves everything ProfPilot will need to prepare an exam. **No AI runs yet:** nothing is generated, files are stored but not read, and the professor's instructions are saved exactly as written. AI generation is the [next phase](#next-phase-ai-generation).

## The flow

1. **Workspace** (`/workspace`). The home page links to **Create assessment** and lists the professor's most recent assessments.
2. **Create assessment** (`/workspace/assessments/new`). One page with these sections:
   - **Course** and **Assessment name**: the only two required fields. A course can be added without leaving the page (**New course**).
   - **Course material**: lectures, notes, readings, assignments.
   - **Previous assessments**: past exams, quizzes, answer keys.
   - **Exam design**: duration, question distribution (MCQ / subjective, always adding up to 100%), number of versions, difficulty.
   - **Additional notes**: specific requests or constraints.
   - **Additional images or attachments**: screenshots, diagrams, graphs, tables.
   - **Tell ProfPilot what you want**: the professor's instructions in their own words.

   Everything except the course and name is optional, and each optional section says so. A live **Assessment summary** shows the current settings and how many files are in each category.
3. **Save draft** keeps the professor on the page and shows **Saving…**, then **Saved** (or **Save failed** with the reason). The first save moves the page to the draft's own address, `/workspace/assessments/[id]/edit`, so reloading reopens it. **Continue** saves, then opens the assessment's overview.
4. **Overview** (`/workspace/assessments/[id]`). A read-only summary: settings, notes, instructions, and the files in each category, with **Edit assessment** and **Delete**.
5. **Edit** (`/workspace/assessments/[id]/edit`). The same form, reopened with every option, text and uploaded file restored.
6. **Assessments list** (`/workspace/assessments`). Every assessment, most recently updated first, with its course, duration, versions, file count and last update. Each row opens its overview.

## The draft model

An assessment is a row in `exam_projects` with `status = 'draft'`. The fields the form fills are:

`course_id`, `exam_name`, `duration_minutes`, `mcq_percentage`, `subjective_percentage`, `number_of_versions`, `difficulty`, `additional_notes`, `professor_prompt`.

Unspecified options are stored as `null`; `number_of_versions` defaults to 1. The AI fields (`enhanced_prompt`, `exam_spec`, `generation_mode`) are left empty for the next phase.

**One assessment is always one draft.** A new assessment gets its `exam_projects` row the first time the professor uploads a file or saves, whichever comes first (`createAssessmentDraft`). Every later upload and save reuses that row; saving only ever updates it (`saveAssessmentDraft`). Double clicks and parallel uploads share the same request, so they can't create a second draft.

**Files are saved as soon as they upload**, independently of **Save draft**. Removing a file deletes it right away. So an assessment's files are never "unsaved", and a draft created by an upload keeps its files even if the professor leaves without saving.

**Deleting an assessment** (overview, **Delete**, then confirm) removes its files from Storage first, including any file an interrupted upload left in its folder. Then it deletes the `exam_projects` row, which deletes its `documents` rows too (`on delete cascade`). If removing the files fails, nothing is deleted.

The server actions are in `frontend/src/lib/assessments/actions.ts` and `frontend/src/lib/documents/actions.ts`.

## Uploaded files

### Categories

| Category | Section | Meaning |
| --- | --- | --- |
| `course_material` | Course material | What students were taught |
| `previous_exam` | Previous assessments | How the professor usually assesses students |
| `additional_attachment` | Additional images or attachments | Images or files the professor wants referenced |

The categories are never merged: each file belongs to exactly one.

### What can be uploaded

PDF, PPTX, DOCX, TXT, PNG, JPG/JPEG and WEBP, up to 25 MB each. These limits are checked three times:

1. in the browser, for instant and clear errors;
2. on the server, which reads the real size and type back from Storage;
3. by the Storage bucket itself.

Adding the same file to a section twice is flagged instead of uploading it again.

### The `documents` table

One row per uploaded file (migration `supabase/migrations/20261003163734_documents_and_storage.sql`):

| Column | Notes |
| --- | --- |
| `id` | Primary key |
| `professor_id` | Owner. Defaults to the signed-in professor (`auth.uid()`); deleted with the profile |
| `course_id` | The assessment's course; set to `null` if the course is deleted |
| `exam_project_id` | The assessment; deleting it deletes the row |
| `category` | One of the three categories above |
| `original_name` | The file name as the professor knows it |
| `storage_path` | Where the file is in the bucket (unique) |
| `mime_type`, `size_bytes` | Read from Storage, not from the browser |
| `created_at` | Upload time |

Indexes cover `professor_id`, `course_id` and `exam_project_id`.

### The private Storage bucket

Files live in the **private** bucket `assessment-files`. It has no public URLs, a 25 MB limit and the seven allowed types. Each file is stored at:

```text
{professor_id}/{exam_project_id}/{category}/{uuid}-{safe file name}
```

The `uuid` keeps every path unique, so files are never overwritten. The safe file name keeps only letters, digits, `.`, `-` and `_`; the original name is kept in `documents.original_name`.

### How an upload works

1. The browser uploads the file straight to Storage with the professor's own session. This avoids routing large files through the Next.js server, whose Server Actions accept at most 1 MB.
2. The `recordUploadedDocument` server action then checks the upload and records it:
   - the path must be inside the professor's own folder;
   - the size and type are read back from Storage and checked again;
   - the course comes from the saved draft, not from the browser.
3. If the record can't be saved, the uploaded file is deleted, so no file is left without a row.

## Security

Every professor can only reach their own data. This is enforced by the database and Storage, not only by the pages:

- **`documents` Row Level Security.** A professor can read, change and delete only their own rows. A new or changed row must:
  - belong to the signed-in professor;
  - point into their own Storage folder;
  - link only to their own course and their own assessment.
- **Storage policies.** A signed-in professor can upload, read and delete objects only when the first folder of the path is their own id. There is no update policy (files are never overwritten), and signed-out visitors have no access at all.
- **Server actions.** Every action checks the session, re-validates its input, and lets Row Level Security decide what it may touch. IDs sent by the browser are never trusted on their own.
- **Pages.** Another professor's assessment, or one that doesn't exist, shows **Assessment not found**. Neither reveals whether the other exists.
- **Keys.** The browser only has the publishable key. The secret / `service_role` key is never used by the frontend.

See [Authentication and data security](authentication.md) for sign-up, sessions, and the other tables' policies.

## How to test the flow

Automated checks, from `frontend/`: `npm run lint`, `npx tsc --noEmit` and `npm run build`; from `backend/`: `pytest`.

To try the flow by hand (about 10 minutes), use two accounts (A and B):

1. As **A**, open **Courses** and add a course. Then **Create assessment**: choose the course, enter a name, and press **Save draft**. The page moves to the draft's address and says **Saved**. Reload: everything is still there.
2. Upload a PDF to each of the three file sections. Try an unsupported file (for example a `.zip`): it is listed as **Not added** with the reason. Remove one file.
3. Set a duration, a question distribution, versions and difficulty; add notes and instructions; press **Continue**. The overview shows all of it.
4. **Edit assessment**: everything is restored. Change the percentage, save, reload: the new percentage remains.
5. Open **Assessments**: the draft is listed with its course, settings, file count and last update.
6. Copy the overview's address. Log in as **B** (another browser or a private window): B sees no courses and no assessments, and A's address shows **Assessment not found**.
7. As **A**, delete the assessment from its overview. It disappears from the list, and its files are removed from the bucket.

## Next phase: AI generation

Not implemented yet. The next phase turns a saved draft into an exam:

```text
Professor input  →  Prompt Interpreter  →  Enhanced Prompt  →  ExamSpec  →  generated exam
```

It will fill `enhanced_prompt`, `exam_spec` and `generation_mode`, and read the uploaded files (parsing, and later course knowledge search with pgvector).

## Known limitations

- **Deleting an account** deletes the professor's rows but not their Storage files. Deleting an assessment from the app does remove its files.
- **The unsaved-changes prompt** appears when the page is reloaded or closed. Links inside the app leave without asking.
- **File contents aren't inspected yet.** A renamed file passes the type check. Files are stored privately and never run.
- **No storage quota per professor**, beyond the 25 MB per-file limit.

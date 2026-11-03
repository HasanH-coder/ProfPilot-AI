# Course Sources

Course Sources is the reusable materials layer for ProfPilot agents. A professor uploads a PDF, slide deck, document, text file, or image once under a course. The existing ingestion service extracts, summarizes, chunks, and embeds it once; assessments and future agents refer to the canonical document id.

## Data model

- A library source is a `documents` row with a `course_id` and no `exam_project_id`.
- Existing direct assessment uploads remain `documents` rows with an `exam_project_id` and continue to work unchanged.
- `assessment_documents` stores the selected reusable document ids for an assessment. Its RLS policy permits only sources owned by the professor and belonging to the assessment's course.
- `assessment_documents` is a second relationship between `exam_projects` and `documents`, so a PostgREST embed of an assessment's own uploads must name its foreign key: `documents!documents_exam_project_id_fkey(...)`. A plain `documents(...)` is ambiguous and fails.
- `match_document_chunks` searches direct uploads plus the reusable documents selected for that assessment. Unselected course sources cannot enter AI context.

Removing a source first sets `deletion_pending`, which the database refuses while any assessment has it selected; only then may Storage delete its file, and the row (with its chunks) goes last. Direct-upload removal never touches course sources.

The browser computes SHA-256 before upload. A partial unique index on professor, course, and digest prevents duplicate canonical source rows, including concurrent uploads. Ingestion recomputes the digest from the private Storage object before trusting it and reuses already-processed chunks when possible.

## Assessment form integration

The Course material card of the assessment form shows `CourseSourceSelector` under the direct uploads, for the chosen course's `ready` sources.

- The create and edit pages load `getSources()` (every course's selectable sources) and, when editing, `getAssessmentSourceDocumentIds(assessmentId)`.
- The selection is part of the form's values, so "Unsaved changes", the card's closed summary and the Assessment summary all count it. Changing the course (by hand or through Set up with AI) clears it.
- Saving calls `setAssessmentSourceDocuments(assessmentId, ids)` right after the draft, with only ids of the chosen course's sources. The database function replaces the selection atomically and marks an existing AI spec stale.
- Direct uploads stay assessment-owned and are combined with the selection by `AssessmentContextService.list_documents()`.

No file copy or new ingestion request is needed when a source is selected.

## Ready for AI without anyone asking

A lecture PDF has every slide's words kept page by page as it's read (from the file itself, with no AI; a scanned lecture's transcribed), and its slides are shown to the AI as images when an exam is written, the way a chat assistant reads a PDF (a PowerPoint file has its pictures read instead; see [the Assessment Agent](assessment-agent.md#reading-the-uploaded-files)). Its lecture knowledge is built as soon as it's uploaded. Sources read before that are brought up to date when Sources opens: the page calls `POST /api/sources/preparation` (with a fresh session), which prepares in the background, two at a time, every source that isn't up to date, and each lecture shows what's under way until it's done. Nothing needs the professor; only a failure shows **Retry** (or **Try again** for a PowerPoint's pictures). Prepared sources cost nothing to check.

## Recovery

- Opening Sources resumes any source left `pending` or `processing`. The backend skips a source it is still reading, and reads one again whose reading was interrupted (for example by a restart). This assumes a single backend process, like the job runner.
- A removal that stopped part-way leaves the source reserved (`deletion_pending`): it can't be selected, isn't searched, and removing it again finishes the job.

## Database rollout

Apply `20261016160000_course_sources.sql` after the existing migrations, then regenerate Supabase TypeScript types. The migration is versioned only; it is not applied automatically or to a live project by this branch.

Keeping every slide's words needs `20261028120000_page_understanding.sql` (the `document_pages` table and `search_document_pages`), applied before the backend that uses it is started.

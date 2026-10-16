# Course Sources

Course Sources is the reusable materials layer for ProfPilot agents. A professor uploads a PDF, slide deck, document, text file, or image once under a course. The existing ingestion service extracts, summarizes, chunks, and embeds it once; assessments and future agents refer to the canonical document id.

## Data model

- A library source is a `documents` row with a `course_id` and no `exam_project_id`.
- Existing direct assessment uploads remain `documents` rows with an `exam_project_id` and continue to work unchanged.
- `assessment_documents` stores the selected reusable document ids for an assessment. Its RLS policy permits only sources owned by the professor and belonging to the assessment's course.
- `match_document_chunks` searches direct uploads plus the reusable documents selected for that assessment. Unselected course sources cannot enter AI context.

Removing a source first sets `deletion_pending`, which the database refuses while any assessment has it selected; only then may Storage delete its file, and the row (with its chunks) goes last. Direct-upload removal never touches course sources.

The browser computes SHA-256 before upload. A partial unique index on professor, course, and digest prevents duplicate canonical source rows, including concurrent uploads. Ingestion recomputes the digest from the private Storage object before trusting it and reuses already-processed chunks when possible.

## Assessment form integration

After the concurrent assessment-form work is complete:

1. Load `getCourseSources(courseId)` and `getAssessmentSourceDocumentIds(assessmentId)`.
2. Render `CourseSourceSelector` with those values and keep its `selectedIds` controlled by the form.
3. When the draft is saved, call `setAssessmentSourceDocuments(assessmentId, selectedIds)`. The database function replaces the selection atomically and marks an existing AI spec stale.
4. Keep the current direct-upload controls. They remain assessment-owned and are automatically combined with the selection by `AssessmentContextService.list_documents()`.

No file copy or new ingestion request is needed when a source is selected. The selector exposes only sources whose processing status is `ready`.

## Database rollout

Apply `20261016160000_course_sources.sql` after the existing migrations, then regenerate Supabase TypeScript types. The migration is versioned only; it is not applied automatically or to a live project by this branch.

-- ProfPilot AI: a reusable Lecture Knowledge Profile per course-material document.
--
-- The profile is a structured analysis of the whole document (topics, concepts,
-- formulas, examples, visuals, misconceptions, relations and Bloom's
-- opportunities, each with its source locations, plus coverage metadata). It is
-- built once per document version and reused by every assessment. The chunks
-- and embeddings stay the authoritative, searchable evidence.
--
-- It lives on the document row, so the existing Row Level Security policies,
-- cascades, Course Sources deletion reservation and identical-file reuse apply
-- to it unchanged. Null means no profile has been built.

alter table public.documents
  add column knowledge jsonb;

comment on column public.documents.knowledge is
  'Lecture Knowledge Profile: {version, content_sha256, status, topics, relations, coverage, warnings, usage, ...}. Built once per document version.';

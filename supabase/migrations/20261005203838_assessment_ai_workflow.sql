-- =============================================================================
-- ProfPilot AI: the Assessment Agent's AI workflow
--
--   documents                 + processing state (parsed, chunked, embedded)
--   document_chunks           searchable pieces of each uploaded file (pgvector)
--   exam_projects             + ExamSpec approval state (draft / approved / stale)
--   assessment_style_profiles cached analysis of the professor's previous exams
--   exams                     a generated (or interactively built) exam
--   exam_versions             Version A, B, C… of an exam
--   exam_sections             sections shared by all versions of an exam
--   exam_questions            structured questions with answers and rubrics
--   question_revisions        what each question looked like before a change
--   ai_runs                   long AI jobs: progress, errors and token usage
--   professor_preferences     a professor's assessment preferences
--   preference_signals        accepted work that personalization learns from
--   exam_builder_messages     the "Build with AI" conversation
--
-- Every table is owned through professor_id and protected by Row Level
-- Security, exactly like the earlier tables: a professor can only read or
-- change their own rows, and can only link them to their own parents.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- pgvector, for searching course material by meaning
-- -----------------------------------------------------------------------------

create extension if not exists vector with schema extensions;


-- -----------------------------------------------------------------------------
-- documents: processing state
-- -----------------------------------------------------------------------------

alter table public.documents
  add column processing_status text not null default 'pending',
  add column processing_error  text,
  add column processed_at      timestamptz,
  -- Files are never overwritten, so a processed file never needs processing again.
  add column content_sha256    text,
  -- Pages for PDFs, slides for PowerPoints.
  add column page_count        integer,
  add column extracted_characters integer,
  -- A short AI overview: title, summary and the topics it covers.
  add column summary           jsonb,
  add constraint documents_processing_status_valid
    check (processing_status in ('pending', 'processing', 'ready', 'failed')),
  add constraint documents_page_count_not_negative
    check (page_count >= 0),
  add constraint documents_extracted_characters_not_negative
    check (extracted_characters >= 0);


-- -----------------------------------------------------------------------------
-- document_chunks: searchable pieces of each file
-- -----------------------------------------------------------------------------

create table public.document_chunks (
  id               uuid primary key default gen_random_uuid(),
  professor_id     uuid not null default auth.uid()
                   references public.profiles (id) on delete cascade,
  document_id      uuid not null references public.documents (id) on delete cascade,
  -- Copied from the document by a trigger (see below), never from the request,
  -- so search can filter by assessment and category without a join.
  exam_project_id  uuid references public.exam_projects (id) on delete cascade,
  category         text not null,
  chunk_index      integer not null,
  content          text not null,
  -- Where the text is in the original file, e.g. "Slide 4" or "Pages 3–4".
  page_start       integer,
  page_end         integer,
  location_label   text,
  token_estimate   integer,
  embedding        extensions.vector(1536),
  created_at       timestamptz not null default now(),

  constraint document_chunks_index_not_negative check (chunk_index >= 0),
  constraint document_chunks_token_estimate_not_negative check (token_estimate >= 0),
  constraint document_chunks_content_not_empty check (length(content) > 0),
  constraint document_chunks_category_valid
    check (category in ('course_material', 'previous_exam', 'additional_attachment')),
  constraint document_chunks_unique_index unique (document_id, chunk_index)
);

create index document_chunks_professor_id_idx on public.document_chunks (professor_id);
create index document_chunks_exam_project_category_idx
  on public.document_chunks (exam_project_id, category);
-- Approximate nearest-neighbour search, for when the material grows large.
create index document_chunks_embedding_idx
  on public.document_chunks using hnsw (embedding extensions.vector_cosine_ops);

-- Fills in exam_project_id and category from the chunk's document. Runs as the
-- signed-in professor, so Row Level Security only finds their own documents.
create function public.set_document_chunk_scope()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  parent record;
begin
  select d.exam_project_id, d.category, d.professor_id
    into parent
    from public.documents d
   where d.id = new.document_id;
  if not found or parent.professor_id <> new.professor_id then
    raise exception 'document not found' using errcode = '42501';
  end if;
  new.exam_project_id := parent.exam_project_id;
  new.category := parent.category;
  return new;
end;
$$;

revoke execute on function public.set_document_chunk_scope() from public, anon, authenticated;

create trigger set_document_chunk_scope
  before insert or update on public.document_chunks
  for each row execute function public.set_document_chunk_scope();

-- The chunks of one assessment that best match a query, most similar first.
-- security invoker: Row Level Security still applies to the caller.
create function public.match_document_chunks(
  p_exam_project_id uuid,
  p_query_embedding extensions.vector(1536),
  p_categories text[] default array['course_material'],
  p_match_count integer default 12
)
returns table (
  id uuid,
  document_id uuid,
  chunk_index integer,
  content text,
  page_start integer,
  page_end integer,
  location_label text,
  similarity double precision
)
language sql
stable
security invoker
set search_path = ''
as $$
  select c.id, c.document_id, c.chunk_index, c.content, c.page_start, c.page_end,
         c.location_label,
         1 - (c.embedding operator(extensions.<=>) p_query_embedding) as similarity
    from public.document_chunks c
   where c.exam_project_id = p_exam_project_id
     and c.category = any (p_categories)
     and c.embedding is not null
   order by c.embedding operator(extensions.<=>) p_query_embedding
   limit least(greatest(p_match_count, 1), 50);
$$;

revoke execute on function public.match_document_chunks(uuid, extensions.vector, text[], integer)
  from public, anon;
grant execute on function public.match_document_chunks(uuid, extensions.vector, text[], integer)
  to authenticated;


-- -----------------------------------------------------------------------------
-- exam_projects: the ExamSpec and its approval state
-- -----------------------------------------------------------------------------
-- enhanced_prompt, exam_spec and generation_mode already exist (initial schema).
--
--   none      not interpreted yet
--   draft     interpreted, waiting for the professor's approval
--   approved  approved by the professor; generation may use it
--   stale     the setup changed after interpretation; it must be redone

alter table public.exam_projects
  add column spec_status       text not null default 'none',
  add column spec_generated_at timestamptz,
  add column spec_approved_at  timestamptz,
  add constraint exam_projects_spec_status_valid
    check (spec_status in ('none', 'draft', 'approved', 'stale')),
  add constraint exam_projects_generation_mode_valid
    check (generation_mode in ('full', 'interactive'));

-- Marks an interpretation stale as soon as anything it was based on changes, so
-- an outdated ExamSpec can never be used silently. It applies to every write
-- path (the form, the voice assistant, the API). An update that sets
-- spec_status itself (a new interpretation or an approval) is left alone.
create function public.mark_exam_spec_stale()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if old.spec_status in ('draft', 'approved')
     and new.spec_status = old.spec_status
     and (new.course_id, new.exam_name, new.duration_minutes, new.mcq_percentage,
          new.subjective_percentage, new.number_of_versions, new.easy_percentage,
          new.medium_percentage, new.hard_percentage, new.additional_notes,
          new.professor_prompt)
         is distinct from
         (old.course_id, old.exam_name, old.duration_minutes, old.mcq_percentage,
          old.subjective_percentage, old.number_of_versions, old.easy_percentage,
          old.medium_percentage, old.hard_percentage, old.additional_notes,
          old.professor_prompt)
  then
    new.spec_status := 'stale';
  end if;
  return new;
end;
$$;

revoke execute on function public.mark_exam_spec_stale() from public, anon, authenticated;

create trigger mark_exam_spec_stale
  before update on public.exam_projects
  for each row execute function public.mark_exam_spec_stale();

-- Adding or removing a file changes what the interpretation was based on too.
create function public.mark_exam_spec_stale_for_documents()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  project_id uuid;
begin
  if tg_op = 'DELETE' then
    project_id := old.exam_project_id;
  else
    project_id := new.exam_project_id;
  end if;
  if project_id is not null then
    update public.exam_projects
       set spec_status = 'stale'
     where id = project_id
       and spec_status in ('draft', 'approved');
  end if;
  return null;
end;
$$;

revoke execute on function public.mark_exam_spec_stale_for_documents()
  from public, anon, authenticated;

create trigger mark_exam_spec_stale_for_documents
  after insert or delete on public.documents
  for each row execute function public.mark_exam_spec_stale_for_documents();


-- -----------------------------------------------------------------------------
-- assessment_style_profiles: what the professor's previous exams look like
-- -----------------------------------------------------------------------------

create table public.assessment_style_profiles (
  id                  uuid primary key default gen_random_uuid(),
  professor_id        uuid not null default auth.uid()
                      references public.profiles (id) on delete cascade,
  exam_project_id     uuid not null unique
                      references public.exam_projects (id) on delete cascade,
  -- Identifies the exact set of previous exams analysed, so the analysis is
  -- only paid for again when that set changes.
  source_hash         text not null,
  source_document_ids uuid[] not null,
  profile             jsonb not null,
  model               text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

create index assessment_style_profiles_professor_id_idx
  on public.assessment_style_profiles (professor_id);


-- -----------------------------------------------------------------------------
-- exams, versions, sections and questions
-- -----------------------------------------------------------------------------

create table public.exams (
  id               uuid primary key default gen_random_uuid(),
  professor_id     uuid not null default auth.uid()
                   references public.profiles (id) on delete cascade,
  -- One exam per assessment; generating again replaces it.
  exam_project_id  uuid not null unique
                   references public.exam_projects (id) on delete cascade,
  mode             text not null,
  --   generating  the full-generation pipeline is running
  --   building    "Build with AI" is in progress
  --   ready       complete and editable
  --   failed      generation stopped; questions already saved are kept
  status           text not null default 'generating',
  -- The approved ExamSpec and enhanced prompt this exam was generated from.
  spec_snapshot    jsonb not null,
  enhanced_prompt_snapshot text,
  plan             jsonb,
  review           jsonb,
  review_status    text not null default 'not_run',
  reviewed_at      timestamptz,
  error_message    text,
  finalized_at     timestamptz,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),

  constraint exams_mode_valid check (mode in ('full', 'interactive')),
  constraint exams_status_valid check (status in ('generating', 'building', 'ready', 'failed')),
  constraint exams_review_status_valid
    check (review_status in ('not_run', 'running', 'passed', 'needs_attention', 'failed'))
);

create index exams_professor_id_idx on public.exams (professor_id);

create table public.exam_versions (
  id           uuid primary key default gen_random_uuid(),
  professor_id uuid not null default auth.uid()
               references public.profiles (id) on delete cascade,
  exam_id      uuid not null references public.exams (id) on delete cascade,
  label        text not null,
  position     integer not null,
  created_at   timestamptz not null default now(),

  constraint exam_versions_label_valid check (label ~ '^[A-Z]$'),
  constraint exam_versions_position_positive check (position > 0),
  constraint exam_versions_unique_label unique (exam_id, label),
  constraint exam_versions_unique_position unique (exam_id, position),
  -- Lets questions reference (version, exam) together, so a question can never
  -- belong to a version of a different exam.
  constraint exam_versions_id_exam_unique unique (id, exam_id)
);

create index exam_versions_professor_id_idx on public.exam_versions (professor_id);

create table public.exam_sections (
  id           uuid primary key default gen_random_uuid(),
  professor_id uuid not null default auth.uid()
               references public.profiles (id) on delete cascade,
  exam_id      uuid not null references public.exams (id) on delete cascade,
  position     integer not null,
  title        text not null,
  instructions text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),

  constraint exam_sections_position_positive check (position > 0),
  constraint exam_sections_title_length check (length(title) between 1 and 200),
  constraint exam_sections_unique_position unique (exam_id, position)
    deferrable initially immediate,
  constraint exam_sections_id_exam_unique unique (id, exam_id)
);

create index exam_sections_professor_id_idx on public.exam_sections (professor_id);

create table public.exam_questions (
  id                    uuid primary key default gen_random_uuid(),
  professor_id          uuid not null default auth.uid()
                        references public.profiles (id) on delete cascade,
  exam_id               uuid not null references public.exams (id) on delete cascade,
  version_id            uuid not null,
  section_id            uuid,
  -- Equivalent questions in different versions share a slot_id.
  slot_id               uuid not null default gen_random_uuid(),
  -- Order within the version. Gaps are fine: numbering follows the order.
  position              integer not null,
  type                  text not null,
  difficulty            text not null,
  points                numeric(6, 2) not null,
  prompt                text not null,
  -- Multiple choice only: [{"id": "A", "text": "…"}, …] and the correct id.
  choices               jsonb,
  correct_choice        text,
  -- [{"label": "a", "prompt": "…", "points": 4, "answer": "…"}, …]
  subparts              jsonb,
  answer                text,
  solution              text,
  -- [{"criterion": "…", "points": 2}, …]
  rubric                jsonb,
  explanation           text,
  concepts              text[] not null default '{}',
  learning_objectives   text[] not null default '{}',
  -- Internal only (never exported): which uploaded material grounds it.
  source_refs           jsonb not null default '[]',
  -- An uploaded image the question shows, such as a diagram.
  figure_document_id    uuid references public.documents (id) on delete set null,
  estimated_minutes     numeric(5, 1),
  --   draft     may still change
  --   approved  approved and locked by the professor
  status                text not null default 'draft',
  -- Set when the question changed in a way that may invalidate its answer.
  needs_solution_review boolean not null default false,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  constraint exam_questions_version_fkey foreign key (version_id, exam_id)
    references public.exam_versions (id, exam_id) on delete cascade,
  constraint exam_questions_section_fkey foreign key (section_id, exam_id)
    references public.exam_sections (id, exam_id),
  constraint exam_questions_type_valid
    check (type in ('mcq', 'short_answer', 'long_answer', 'problem')),
  constraint exam_questions_difficulty_valid
    check (difficulty in ('easy', 'medium', 'hard')),
  constraint exam_questions_status_valid check (status in ('draft', 'approved')),
  constraint exam_questions_points_range check (points > 0 and points <= 1000),
  constraint exam_questions_position_positive check (position > 0),
  constraint exam_questions_prompt_length check (length(prompt) between 1 and 20000),
  constraint exam_questions_estimated_minutes_positive check (estimated_minutes > 0),
  -- A multiple-choice question has 2–8 choices and a correct answer that is one
  -- of them; any other question has neither. An answer key can't point at a
  -- choice that doesn't exist.
  constraint exam_questions_choices_shape check (
    case
      when type = 'mcq' then
        choices is not null
        and jsonb_typeof(choices) = 'array'
        and jsonb_array_length(choices) between 2 and 8
        and correct_choice is not null
        and choices @> jsonb_build_array(jsonb_build_object('id', correct_choice))
      else choices is null and correct_choice is null
    end
  ),
  constraint exam_questions_subparts_shape
    check (subparts is null or jsonb_typeof(subparts) = 'array'),
  constraint exam_questions_rubric_shape
    check (rubric is null or jsonb_typeof(rubric) = 'array'),
  constraint exam_questions_source_refs_shape check (jsonb_typeof(source_refs) = 'array'),
  constraint exam_questions_unique_position unique (version_id, position)
    deferrable initially immediate,
  constraint exam_questions_unique_slot unique (version_id, slot_id)
);

create index exam_questions_professor_id_idx on public.exam_questions (professor_id);
create index exam_questions_exam_id_idx on public.exam_questions (exam_id);
-- Match the composite foreign keys, so checks and cascades don't scan the table.
create index exam_questions_version_exam_idx on public.exam_questions (version_id, exam_id);
create index exam_questions_section_exam_idx on public.exam_questions (section_id, exam_id);
create index exam_questions_figure_document_id_idx on public.exam_questions (figure_document_id);

create table public.question_revisions (
  id              uuid primary key default gen_random_uuid(),
  professor_id    uuid not null default auth.uid()
                  references public.profiles (id) on delete cascade,
  question_id     uuid not null references public.exam_questions (id) on delete cascade,
  revision_number integer not null,
  --   ai_revision           the professor asked AI to change the question
  --   manual_edit           the professor edited it by hand
  --   ai_review_fix         the quality check repaired an obvious problem
  --   solution_regenerated  AI rewrote the answer key for the current question
  --   restore               an earlier revision was restored
  source          text not null,
  instruction     text,
  -- The whole question as it was BEFORE this change, so it can be restored.
  snapshot        jsonb not null,
  created_at      timestamptz not null default now(),

  constraint question_revisions_source_valid check (
    source in ('ai_revision', 'manual_edit', 'ai_review_fix', 'solution_regenerated', 'restore')
  ),
  constraint question_revisions_number_positive check (revision_number > 0),
  constraint question_revisions_unique_number unique (question_id, revision_number)
);

create index question_revisions_professor_id_idx on public.question_revisions (professor_id);


-- -----------------------------------------------------------------------------
-- ai_runs: long AI jobs, their progress and their cost
-- -----------------------------------------------------------------------------

create table public.ai_runs (
  id              uuid primary key default gen_random_uuid(),
  professor_id    uuid not null default auth.uid()
                  references public.profiles (id) on delete cascade,
  exam_project_id uuid not null references public.exam_projects (id) on delete cascade,
  exam_id         uuid references public.exams (id) on delete cascade,
  kind            text not null,
  status          text not null default 'running',
  -- What the job is doing now, e.g. "planning" or "reviewing". No percentages.
  stage           text,
  -- A message that is safe to show the professor. Never internals.
  error_message   text,
  input_tokens    integer not null default 0,
  output_tokens   integer not null default 0,
  started_at      timestamptz not null default now(),
  -- Updated while the job works; a job that stops updating was interrupted.
  heartbeat_at    timestamptz not null default now(),
  finished_at     timestamptz,

  constraint ai_runs_kind_valid check (
    kind in ('document_processing', 'interpretation', 'generation', 'review', 'versioning')
  ),
  constraint ai_runs_status_valid check (status in ('running', 'succeeded', 'failed')),
  constraint ai_runs_tokens_not_negative check (input_tokens >= 0 and output_tokens >= 0)
);

create index ai_runs_professor_id_idx on public.ai_runs (professor_id);
create index ai_runs_exam_project_id_idx on public.ai_runs (exam_project_id, started_at desc);
create index ai_runs_exam_id_idx on public.ai_runs (exam_id);
-- At most one running job of each kind per assessment.
create unique index ai_runs_one_running_per_kind
  on public.ai_runs (exam_project_id, kind) where status = 'running';


-- -----------------------------------------------------------------------------
-- Personalization
-- -----------------------------------------------------------------------------

create table public.professor_preferences (
  professor_id     uuid primary key default auth.uid()
                   references public.profiles (id) on delete cascade,
  -- Preferences the professor wrote themselves.
  explicit_notes   text,
  -- Tendencies learned from finalized exams and accepted AI revisions.
  learned          jsonb not null default '{}',
  learning_enabled boolean not null default true,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),

  constraint professor_preferences_notes_length check (length(explicit_notes) <= 2000)
);

-- Only accepted work is recorded here; drafts and rejected suggestions never are.
create table public.preference_signals (
  id              uuid primary key default gen_random_uuid(),
  professor_id    uuid not null default auth.uid()
                  references public.profiles (id) on delete cascade,
  exam_project_id uuid references public.exam_projects (id) on delete set null,
  kind            text not null,
  payload         jsonb not null,
  created_at      timestamptz not null default now(),

  constraint preference_signals_kind_valid
    check (kind in ('exam_finalized', 'revision_accepted')),
  constraint preference_signals_payload_object check (jsonb_typeof(payload) = 'object')
);

create index preference_signals_professor_id_idx
  on public.preference_signals (professor_id, created_at desc);
create index preference_signals_exam_project_id_idx
  on public.preference_signals (exam_project_id);


-- -----------------------------------------------------------------------------
-- exam_builder_messages: the "Build with AI" conversation
-- -----------------------------------------------------------------------------

create table public.exam_builder_messages (
  id           uuid primary key default gen_random_uuid(),
  professor_id uuid not null default auth.uid()
               references public.profiles (id) on delete cascade,
  exam_id      uuid not null references public.exams (id) on delete cascade,
  role         text not null,
  channel      text not null default 'text',
  content      text not null,
  created_at   timestamptz not null default now(),

  constraint exam_builder_messages_role_valid check (role in ('professor', 'assistant')),
  constraint exam_builder_messages_channel_valid check (channel in ('text', 'voice')),
  constraint exam_builder_messages_content_length check (length(content) between 1 and 8000)
);

create index exam_builder_messages_exam_id_idx
  on public.exam_builder_messages (exam_id, created_at);
create index exam_builder_messages_professor_id_idx
  on public.exam_builder_messages (professor_id);


-- -----------------------------------------------------------------------------
-- updated_at
-- -----------------------------------------------------------------------------

create trigger set_assessment_style_profiles_updated_at
  before update on public.assessment_style_profiles
  for each row execute function public.set_updated_at();

create trigger set_exams_updated_at
  before update on public.exams
  for each row execute function public.set_updated_at();

create trigger set_exam_sections_updated_at
  before update on public.exam_sections
  for each row execute function public.set_updated_at();

create trigger set_exam_questions_updated_at
  before update on public.exam_questions
  for each row execute function public.set_updated_at();

create trigger set_professor_preferences_updated_at
  before update on public.professor_preferences
  for each row execute function public.set_updated_at();


-- -----------------------------------------------------------------------------
-- Reordering questions
-- -----------------------------------------------------------------------------

-- Gives every question of a version its new position in one statement (the
-- position constraint is checked once, at the end). The list must contain each
-- of the version's questions exactly once.
create function public.reorder_exam_questions(p_version_id uuid, p_question_ids uuid[])
returns void
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if cardinality(p_question_ids) is null
     or (select count(distinct x) from unnest(p_question_ids) as x) <> cardinality(p_question_ids)
     or (select count(*) from public.exam_questions q where q.version_id = p_version_id)
        <> cardinality(p_question_ids)
     or exists (
       select 1 from unnest(p_question_ids) as x
        where not exists (
          select 1 from public.exam_questions q where q.id = x and q.version_id = p_version_id
        )
     )
  then
    raise exception 'the new order must list each question of the version once'
      using errcode = '22023';
  end if;

  update public.exam_questions q
     set position = o.ord
    from unnest(p_question_ids) with ordinality as o (id, ord)
   where q.id = o.id
     and q.version_id = p_version_id;
end;
$$;

revoke execute on function public.reorder_exam_questions(uuid, uuid[]) from public, anon;
grant execute on function public.reorder_exam_questions(uuid, uuid[]) to authenticated;


-- -----------------------------------------------------------------------------
-- Row Level Security
-- -----------------------------------------------------------------------------

alter table public.document_chunks enable row level security;
alter table public.assessment_style_profiles enable row level security;
alter table public.exams enable row level security;
alter table public.exam_versions enable row level security;
alter table public.exam_sections enable row level security;
alter table public.exam_questions enable row level security;
alter table public.question_revisions enable row level security;
alter table public.ai_runs enable row level security;
alter table public.professor_preferences enable row level security;
alter table public.preference_signals enable row level security;
alter table public.exam_builder_messages enable row level security;

-- document_chunks: own chunks of own documents. Never updated in place: a
-- document is re-chunked by deleting its chunks and adding new ones.

create policy "Professors can view their own document chunks"
  on public.document_chunks for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can add chunks to their own documents"
  on public.document_chunks for insert
  to authenticated
  with check (
    (select auth.uid()) = professor_id
    and document_id in (
      select documents.id from public.documents
      where documents.professor_id = (select auth.uid())
    )
  );

create policy "Professors can delete their own document chunks"
  on public.document_chunks for delete
  to authenticated
  using ((select auth.uid()) = professor_id);

-- assessment_style_profiles

create policy "Professors can view their own style profiles"
  on public.assessment_style_profiles for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can create style profiles for their own assessments"
  on public.assessment_style_profiles for insert
  to authenticated
  with check (
    (select auth.uid()) = professor_id
    and exam_project_id in (
      select exam_projects.id from public.exam_projects
      where exam_projects.professor_id = (select auth.uid())
    )
  );

create policy "Professors can update their own style profiles"
  on public.assessment_style_profiles for update
  to authenticated
  using ((select auth.uid()) = professor_id)
  with check (
    (select auth.uid()) = professor_id
    and exam_project_id in (
      select exam_projects.id from public.exam_projects
      where exam_projects.professor_id = (select auth.uid())
    )
  );

create policy "Professors can delete their own style profiles"
  on public.assessment_style_profiles for delete
  to authenticated
  using ((select auth.uid()) = professor_id);

-- exams

create policy "Professors can view their own exams"
  on public.exams for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can create exams for their own assessments"
  on public.exams for insert
  to authenticated
  with check (
    (select auth.uid()) = professor_id
    and exam_project_id in (
      select exam_projects.id from public.exam_projects
      where exam_projects.professor_id = (select auth.uid())
    )
  );

create policy "Professors can update their own exams"
  on public.exams for update
  to authenticated
  using ((select auth.uid()) = professor_id)
  with check (
    (select auth.uid()) = professor_id
    and exam_project_id in (
      select exam_projects.id from public.exam_projects
      where exam_projects.professor_id = (select auth.uid())
    )
  );

create policy "Professors can delete their own exams"
  on public.exams for delete
  to authenticated
  using ((select auth.uid()) = professor_id);

-- exam_versions

create policy "Professors can view their own exam versions"
  on public.exam_versions for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can create versions of their own exams"
  on public.exam_versions for insert
  to authenticated
  with check (
    (select auth.uid()) = professor_id
    and exam_id in (
      select exams.id from public.exams where exams.professor_id = (select auth.uid())
    )
  );

create policy "Professors can update their own exam versions"
  on public.exam_versions for update
  to authenticated
  using ((select auth.uid()) = professor_id)
  with check (
    (select auth.uid()) = professor_id
    and exam_id in (
      select exams.id from public.exams where exams.professor_id = (select auth.uid())
    )
  );

create policy "Professors can delete their own exam versions"
  on public.exam_versions for delete
  to authenticated
  using ((select auth.uid()) = professor_id);

-- exam_sections

create policy "Professors can view their own exam sections"
  on public.exam_sections for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can create sections of their own exams"
  on public.exam_sections for insert
  to authenticated
  with check (
    (select auth.uid()) = professor_id
    and exam_id in (
      select exams.id from public.exams where exams.professor_id = (select auth.uid())
    )
  );

create policy "Professors can update their own exam sections"
  on public.exam_sections for update
  to authenticated
  using ((select auth.uid()) = professor_id)
  with check (
    (select auth.uid()) = professor_id
    and exam_id in (
      select exams.id from public.exams where exams.professor_id = (select auth.uid())
    )
  );

create policy "Professors can delete their own exam sections"
  on public.exam_sections for delete
  to authenticated
  using ((select auth.uid()) = professor_id);

-- exam_questions: the composite foreign keys keep the version and section in the
-- same exam, so checking the exam is enough. A figure must be the professor's own.

create policy "Professors can view their own exam questions"
  on public.exam_questions for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can create questions in their own exams"
  on public.exam_questions for insert
  to authenticated
  with check (
    (select auth.uid()) = professor_id
    and exam_id in (
      select exams.id from public.exams where exams.professor_id = (select auth.uid())
    )
    and (
      figure_document_id is null
      or figure_document_id in (
        select documents.id from public.documents
        where documents.professor_id = (select auth.uid())
      )
    )
  );

create policy "Professors can update their own exam questions"
  on public.exam_questions for update
  to authenticated
  using ((select auth.uid()) = professor_id)
  with check (
    (select auth.uid()) = professor_id
    and exam_id in (
      select exams.id from public.exams where exams.professor_id = (select auth.uid())
    )
    and (
      figure_document_id is null
      or figure_document_id in (
        select documents.id from public.documents
        where documents.professor_id = (select auth.uid())
      )
    )
  );

create policy "Professors can delete their own exam questions"
  on public.exam_questions for delete
  to authenticated
  using ((select auth.uid()) = professor_id);

-- question_revisions: history is written, read and deleted, never rewritten.

create policy "Professors can view their own question revisions"
  on public.question_revisions for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can record revisions of their own questions"
  on public.question_revisions for insert
  to authenticated
  with check (
    (select auth.uid()) = professor_id
    and question_id in (
      select exam_questions.id from public.exam_questions
      where exam_questions.professor_id = (select auth.uid())
    )
  );

create policy "Professors can delete their own question revisions"
  on public.question_revisions for delete
  to authenticated
  using ((select auth.uid()) = professor_id);

-- ai_runs

create policy "Professors can view their own AI runs"
  on public.ai_runs for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can start AI runs for their own assessments"
  on public.ai_runs for insert
  to authenticated
  with check (
    (select auth.uid()) = professor_id
    and exam_project_id in (
      select exam_projects.id from public.exam_projects
      where exam_projects.professor_id = (select auth.uid())
    )
    and (
      exam_id is null
      or exam_id in (
        select exams.id from public.exams where exams.professor_id = (select auth.uid())
      )
    )
  );

create policy "Professors can update their own AI runs"
  on public.ai_runs for update
  to authenticated
  using ((select auth.uid()) = professor_id)
  with check (
    (select auth.uid()) = professor_id
    and exam_project_id in (
      select exam_projects.id from public.exam_projects
      where exam_projects.professor_id = (select auth.uid())
    )
    and (
      exam_id is null
      or exam_id in (
        select exams.id from public.exams where exams.professor_id = (select auth.uid())
      )
    )
  );

create policy "Professors can delete their own AI runs"
  on public.ai_runs for delete
  to authenticated
  using ((select auth.uid()) = professor_id);

-- professor_preferences

create policy "Professors can view their own preferences"
  on public.professor_preferences for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can create their own preferences"
  on public.professor_preferences for insert
  to authenticated
  with check ((select auth.uid()) = professor_id);

create policy "Professors can update their own preferences"
  on public.professor_preferences for update
  to authenticated
  using ((select auth.uid()) = professor_id)
  with check ((select auth.uid()) = professor_id);

create policy "Professors can delete their own preferences"
  on public.professor_preferences for delete
  to authenticated
  using ((select auth.uid()) = professor_id);

-- preference_signals

create policy "Professors can view their own preference signals"
  on public.preference_signals for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can record their own preference signals"
  on public.preference_signals for insert
  to authenticated
  with check (
    (select auth.uid()) = professor_id
    and (
      exam_project_id is null
      or exam_project_id in (
        select exam_projects.id from public.exam_projects
        where exam_projects.professor_id = (select auth.uid())
      )
    )
  );

create policy "Professors can delete their own preference signals"
  on public.preference_signals for delete
  to authenticated
  using ((select auth.uid()) = professor_id);

-- exam_builder_messages

create policy "Professors can view their own builder messages"
  on public.exam_builder_messages for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can add builder messages to their own exams"
  on public.exam_builder_messages for insert
  to authenticated
  with check (
    (select auth.uid()) = professor_id
    and exam_id in (
      select exams.id from public.exams where exams.professor_id = (select auth.uid())
    )
  );

create policy "Professors can delete their own builder messages"
  on public.exam_builder_messages for delete
  to authenticated
  using ((select auth.uid()) = professor_id);


-- -----------------------------------------------------------------------------
-- Table privileges (see the initial schema for why they are explicit)
-- -----------------------------------------------------------------------------

revoke all on
  public.document_chunks, public.assessment_style_profiles, public.exams,
  public.exam_versions, public.exam_sections, public.exam_questions,
  public.question_revisions, public.ai_runs, public.professor_preferences,
  public.preference_signals, public.exam_builder_messages
  from anon, authenticated;

grant select, insert, delete on public.document_chunks to authenticated;
grant select, insert, update, delete on public.assessment_style_profiles to authenticated;
grant select, insert, update, delete on public.exams to authenticated;
grant select, insert, update, delete on public.exam_versions to authenticated;
grant select, insert, update, delete on public.exam_sections to authenticated;
grant select, insert, update, delete on public.exam_questions to authenticated;
grant select, insert, delete on public.question_revisions to authenticated;
grant select, insert, update, delete on public.ai_runs to authenticated;
grant select, insert, update, delete on public.professor_preferences to authenticated;
grant select, insert, delete on public.preference_signals to authenticated;
grant select, insert, delete on public.exam_builder_messages to authenticated;

grant select, insert, update, delete on
  public.document_chunks, public.assessment_style_profiles, public.exams,
  public.exam_versions, public.exam_sections, public.exam_questions,
  public.question_revisions, public.ai_runs, public.professor_preferences,
  public.preference_signals, public.exam_builder_messages
  to service_role;

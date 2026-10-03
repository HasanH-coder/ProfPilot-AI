-- =============================================================================
-- ProfPilot AI: uploaded documents
--
--   documents          one row per file a professor uploads for an assessment
--   assessment-files   private Storage bucket that holds the files themselves
--
-- Files are stored at {professor_id}/{exam_project_id}/{category}/{file}.
-- Row Level Security on both the table and the bucket keeps every professor
-- inside their own rows and their own folder.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- documents
-- -----------------------------------------------------------------------------

create table public.documents (
  id              uuid primary key default gen_random_uuid(),
  professor_id    uuid not null default auth.uid()
                  references public.profiles (id) on delete cascade,
  course_id       uuid references public.courses (id) on delete set null,
  exam_project_id uuid references public.exam_projects (id) on delete cascade,
  -- course_material:       what students were taught
  -- previous_exam:         how the professor usually assesses students
  -- additional_attachment: images or files the professor wants referenced
  category        text not null,
  original_name   text not null,
  -- Where the file is in the assessment-files bucket. One row per file.
  storage_path    text not null unique,
  mime_type       text,
  size_bytes      bigint,
  created_at      timestamptz not null default now(),

  constraint documents_category_valid
    check (category in ('course_material', 'previous_exam', 'additional_attachment')),
  -- A CHECK passes when the value is NULL, so an unknown size is allowed.
  constraint documents_size_not_negative
    check (size_bytes >= 0)
);

create index documents_professor_id_idx on public.documents (professor_id);
create index documents_course_id_idx on public.documents (course_id);
create index documents_exam_project_id_idx on public.documents (exam_project_id);


-- -----------------------------------------------------------------------------
-- Row Level Security for documents
-- -----------------------------------------------------------------------------

alter table public.documents enable row level security;

create policy "Professors can view their own documents"
  on public.documents for select
  to authenticated
  using ((select auth.uid()) = professor_id);

-- A new or changed row must belong to the professor, point into the
-- professor's own Storage folder, and link only to their own course and
-- exam project. Ids sent by the browser are never trusted on their own.
create policy "Professors can add their own documents"
  on public.documents for insert
  to authenticated
  with check (
    (select auth.uid()) = professor_id
    and split_part(storage_path, '/', 1) = (select auth.uid())::text
    and (
      course_id is null
      or course_id in (
        select courses.id from public.courses
        where courses.professor_id = (select auth.uid())
      )
    )
    and (
      exam_project_id is null
      or exam_project_id in (
        select exam_projects.id from public.exam_projects
        where exam_projects.professor_id = (select auth.uid())
      )
    )
  );

create policy "Professors can update their own documents"
  on public.documents for update
  to authenticated
  using ((select auth.uid()) = professor_id)
  with check (
    (select auth.uid()) = professor_id
    and split_part(storage_path, '/', 1) = (select auth.uid())::text
    and (
      course_id is null
      or course_id in (
        select courses.id from public.courses
        where courses.professor_id = (select auth.uid())
      )
    )
    and (
      exam_project_id is null
      or exam_project_id in (
        select exam_projects.id from public.exam_projects
        where exam_projects.professor_id = (select auth.uid())
      )
    )
  );

create policy "Professors can delete their own documents"
  on public.documents for delete
  to authenticated
  using ((select auth.uid()) = professor_id);

-- Signed-out visitors (anon) get no access; see the initial schema for why
-- privileges are granted explicitly.
revoke all on public.documents from anon, authenticated;
grant select, insert, update, delete on public.documents to authenticated;
grant select, insert, update, delete on public.documents to service_role;


-- -----------------------------------------------------------------------------
-- Storage bucket for the files
-- -----------------------------------------------------------------------------

-- Private: files are only reachable through the policies below, never by a
-- public URL. Storage itself rejects files over 25 MB and other file types.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'assessment-files',
  'assessment-files',
  false,
  26214400, -- 25 MB
  array[
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'text/plain',
    'image/png',
    'image/jpeg',
    'image/webp'
  ]
);

-- The first folder of every path is the professor's id, so each professor can
-- only upload, read and delete inside their own folder. Files are never
-- overwritten (no upsert), so no update policy is needed.

create policy "Professors can upload assessment files to their own folder"
  on storage.objects for insert
  to authenticated
  with check (
    bucket_id = 'assessment-files'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

create policy "Professors can read their own assessment files"
  on storage.objects for select
  to authenticated
  using (
    bucket_id = 'assessment-files'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

create policy "Professors can delete their own assessment files"
  on storage.objects for delete
  to authenticated
  using (
    bucket_id = 'assessment-files'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

-- ProfPilot AI: reusable course sources.
--
-- Course-owned documents keep using the existing private Storage bucket,
-- ingestion pipeline and document_chunks table. Assessments select them by id
-- through this join table; assessment-owned uploads remain unchanged.

-- Reserve a source before removing its Storage object. Selection and deletion
-- lock the same document row, so an assessment cannot select a disappearing file.
alter table public.documents
  add column deletion_pending boolean not null default false;

create table public.assessment_documents (
  exam_project_id uuid not null
    references public.exam_projects (id) on delete cascade,
  document_id uuid not null
    references public.documents (id) on delete restrict,
  professor_id uuid not null default auth.uid()
    references public.profiles (id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (exam_project_id, document_id)
);

create index assessment_documents_professor_id_idx
  on public.assessment_documents (professor_id);
create index assessment_documents_document_id_idx
  on public.assessment_documents (document_id);

-- A browser-provided digest is only a duplicate-upload hint. Ingestion always
-- recomputes it from Storage before trusting it. The index prevents two
-- canonical copies of the same course source from surviving a race.
create unique index documents_unique_course_source_digest_idx
  on public.documents (professor_id, course_id, content_sha256)
  where exam_project_id is null
    and course_id is not null
    and category = 'course_material'
    and content_sha256 is not null;

alter table public.assessment_documents enable row level security;

create policy "Professors can view their assessment sources"
  on public.assessment_documents for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can select their course sources"
  on public.assessment_documents for insert
  to authenticated
  with check (
    (select auth.uid()) = professor_id
    and exists (
      select 1
      from public.exam_projects e
      join public.documents d on d.id = assessment_documents.document_id
      where e.id = assessment_documents.exam_project_id
        and e.professor_id = (select auth.uid())
        and d.professor_id = (select auth.uid())
        and d.exam_project_id is null
        and d.category = 'course_material'
        and not d.deletion_pending
        and d.course_id = e.course_id
    )
  );

create policy "Professors can unselect their assessment sources"
  on public.assessment_documents for delete
  to authenticated
  using ((select auth.uid()) = professor_id);

revoke all on public.assessment_documents from anon, authenticated;
grant select, insert, delete on public.assessment_documents to authenticated;
grant select, insert, delete on public.assessment_documents to service_role;

-- Replace an assessment's selected course sources atomically. This is the
-- integration seam for the assessment form; an invalid id rolls back the
-- whole operation and preserves the previous selection.
create function public.set_assessment_source_documents(
  p_exam_project_id uuid,
  p_document_ids uuid[] default array[]::uuid[]
)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
declare
  expected_count integer;
  valid_count integer;
begin
  -- Serialize replacement requests and changes to the assessment's course.
  perform 1 from public.exam_projects e
  where e.id = p_exam_project_id
    and e.professor_id = (select auth.uid())
  for update;
  if not found then
    raise exception 'assessment not found' using errcode = '42501';
  end if;

  select count(*) into expected_count
  from (select distinct unnest(coalesce(p_document_ids, array[]::uuid[])) as id) ids;

  select count(*) into valid_count
  from (
    select distinct d.id
    from public.documents d
    join public.exam_projects e on e.id = p_exam_project_id
    where d.id = any(coalesce(p_document_ids, array[]::uuid[]))
      and d.professor_id = (select auth.uid())
      and d.exam_project_id is null
      and d.category = 'course_material'
      and not d.deletion_pending
      and d.course_id = e.course_id
  ) valid;

  if valid_count <> expected_count then
    raise exception 'course source not found' using errcode = '42501';
  end if;

  delete from public.assessment_documents
  where exam_project_id = p_exam_project_id
    and professor_id = (select auth.uid())
    and not (document_id = any(coalesce(p_document_ids, array[]::uuid[])));

  insert into public.assessment_documents (exam_project_id, document_id, professor_id)
  select p_exam_project_id, id, (select auth.uid())
  from (select distinct unnest(coalesce(p_document_ids, array[]::uuid[])) as id) ids
  on conflict (exam_project_id, document_id) do nothing;
end;
$$;

revoke execute on function public.set_assessment_source_documents(uuid, uuid[])
  from public, anon;
grant execute on function public.set_assessment_source_documents(uuid, uuid[])
  to authenticated;

-- Adding or removing a selected source invalidates an already-generated spec.
create trigger mark_exam_spec_stale_for_assessment_documents
  after insert or delete on public.assessment_documents
  for each row execute function public.mark_exam_spec_stale_for_documents();

create function public.check_assessment_source_selection()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  project public.exam_projects;
  source public.documents;
begin
  select * into project from public.exam_projects
  where id = new.exam_project_id and professor_id = (select auth.uid())
  for update;
  select * into source from public.documents
  where id = new.document_id and professor_id = (select auth.uid())
  for update;
  if project.id is null or source.id is null
     or source.exam_project_id is not null
     or source.category <> 'course_material'
     or source.course_id is distinct from project.course_id
     or source.course_id is null or source.deletion_pending then
    raise exception 'course source not found' using errcode = '42501';
  end if;
  return new;
end;
$$;
revoke execute on function public.check_assessment_source_selection() from public, anon, authenticated;
create trigger check_assessment_source_selection
  before insert on public.assessment_documents
  for each row execute function public.check_assessment_source_selection();

-- A referenced source cannot be moved, converted to a direct upload, or
-- reserved for deletion. Processing metadata remains independently updatable.
create function public.protect_referenced_course_source()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if (new.professor_id, new.course_id, new.exam_project_id, new.category,
      new.storage_path, new.deletion_pending)
     is distinct from
     (old.professor_id, old.course_id, old.exam_project_id, old.category,
      old.storage_path, old.deletion_pending)
     and exists (select 1 from public.assessment_documents where document_id = old.id) then
    raise exception 'Unselect this source from assessments before changing or removing it.'
      using errcode = '23503';
  end if;
  return new;
end;
$$;
revoke execute on function public.protect_referenced_course_source() from public, anon, authenticated;
create trigger protect_referenced_course_source
  before update on public.documents
  for each row execute function public.protect_referenced_course_source();

-- Keep the existing owner-folder policy; this additional restriction prevents
-- direct Storage calls (including legacy upload cleanup) from deleting a
-- canonical source until its row has been safely reserved for deletion.
create policy "Course source objects require a deletion reservation"
  on storage.objects as restrictive for delete to authenticated
  using (
    bucket_id <> 'assessment-files'
    or not exists (
      select 1 from public.documents d
      where d.storage_path = storage.objects.name
        and d.exam_project_id is null
        and d.course_id is not null
        and d.category = 'course_material'
        and not d.deletion_pending
    )
  );

create function public.clear_sources_on_assessment_course_change()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.course_id is distinct from old.course_id then
    delete from public.assessment_documents where exam_project_id = new.id;
  end if;
  return null;
end;
$$;
revoke execute on function public.clear_sources_on_assessment_course_change() from public, anon, authenticated;
create trigger clear_sources_on_assessment_course_change
  after update of course_id on public.exam_projects
  for each row execute function public.clear_sources_on_assessment_course_change();

-- Search direct uploads plus only the reusable sources explicitly selected for
-- this assessment. RLS on chunks, documents and the join table still applies.
create or replace function public.match_document_chunks(
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
    join public.documents d on d.id = c.document_id
    join public.exam_projects e on e.id = p_exam_project_id
   where c.professor_id = (select auth.uid())
     and d.professor_id = (select auth.uid())
     and e.professor_id = (select auth.uid())
     and d.category = any (p_categories)
     and not d.deletion_pending
     and c.embedding is not null
     and (
       d.exam_project_id = e.id
       or (d.exam_project_id is null and d.course_id = e.course_id and d.category = 'course_material' and exists (
         select 1 from public.assessment_documents ad
         where ad.exam_project_id = e.id
           and ad.document_id = c.document_id
           and ad.professor_id = (select auth.uid())
       ))
     )
   order by c.embedding operator(extensions.<=>) p_query_embedding
   limit least(greatest(p_match_count, 1), 50);
$$;


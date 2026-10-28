-- Page-by-page understanding of course material.
--
-- Every page of a lecture PDF is rendered as an image and read by a vision model together with
-- its text layer: its text, definitions, formulas, tables, diagrams, charts and examples. Each
-- page's structured, checked analysis is stored here, one row per page, so that:
--   * every page is accounted for (a document isn't fully analysed while a page is pending or failed);
--   * work is resumable and never paid for twice (rows are keyed by page and reused for the same file);
--   * questions can be traced to the exact page and its analysis;
--   * pages can be searched by their words (full text) as well as by meaning (the chunks' embeddings).

create table public.document_pages (
  id                uuid primary key default gen_random_uuid(),
  professor_id      uuid not null default auth.uid()
                    references public.profiles (id) on delete cascade,
  document_id       uuid not null references public.documents (id) on delete cascade,
  page_number       integer not null,
  -- pending: not analysed yet; analysed; blank: nothing to analyse; failed: tried, and couldn't be.
  status            text not null default 'pending',
  -- The page's own text, as extracted from the file (empty for a scanned page).
  text_layer        text,
  -- The structured analysis (see backend/app/services/page_understanding.py), and the checks run on it.
  analysis          jsonb,
  verification      jsonb,
  -- The analysis as plain text, for full-text search.
  search_text       text,
  search            tsvector generated always as (to_tsvector('english', coalesce(search_text, ''))) stored,
  error             text,
  attempts          integer not null default 0,
  -- The file version and the analysis version this row describes.
  content_sha256    text,
  analysis_version  integer not null default 1,
  model             text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),

  constraint document_pages_page_positive check (page_number > 0),
  constraint document_pages_status_valid check (status in ('pending', 'analysed', 'blank', 'failed')),
  constraint document_pages_attempts_not_negative check (attempts >= 0),
  constraint document_pages_unique_page unique (document_id, page_number)
);

create index document_pages_professor_id_idx on public.document_pages (professor_id);
create index document_pages_document_status_idx on public.document_pages (document_id, status);
create index document_pages_search_idx on public.document_pages using gin (search);

create trigger set_document_pages_updated_at
  before update on public.document_pages
  for each row execute function public.set_updated_at();

alter table public.document_pages enable row level security;

create policy "Professors can view their own document pages"
  on public.document_pages for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can add pages to their own documents"
  on public.document_pages for insert
  to authenticated
  with check (
    (select auth.uid()) = professor_id
    and document_id in (
      select documents.id from public.documents
      where documents.professor_id = (select auth.uid())
    )
  );

create policy "Professors can update their own document pages"
  on public.document_pages for update
  to authenticated
  using ((select auth.uid()) = professor_id)
  with check (
    (select auth.uid()) = professor_id
    and document_id in (
      select documents.id from public.documents
      where documents.professor_id = (select auth.uid())
    )
  );

create policy "Professors can delete their own document pages"
  on public.document_pages for delete
  to authenticated
  using ((select auth.uid()) = professor_id);

revoke all on public.document_pages from anon, authenticated;
grant select, insert, update, delete on public.document_pages to authenticated;

-- The analysed pages of one assessment's course material that best match some words, best first:
-- the full-text half of hybrid retrieval (the chunks' embeddings are the other half). Same scope
-- as match_document_chunks: the assessment's own uploads, plus only the sources selected for it.
create function public.search_document_pages(
  p_exam_project_id uuid,
  p_query text,
  p_match_count integer default 8
)
returns table (
  id uuid,
  document_id uuid,
  page_number integer,
  search_text text,
  rank real
)
language sql
stable
security invoker
set search_path = ''
as $$
  select p.id, p.document_id, p.page_number, p.search_text,
         ts_rank(p.search, websearch_to_tsquery('english', p_query)) as rank
    from public.document_pages p
    join public.documents d on d.id = p.document_id
    join public.exam_projects e on e.id = p_exam_project_id
   where p.professor_id = (select auth.uid())
     and d.professor_id = (select auth.uid())
     and e.professor_id = (select auth.uid())
     and p.status = 'analysed'
     and d.category = 'course_material'
     and not d.deletion_pending
     and p.search @@ websearch_to_tsquery('english', p_query)
     and (
       d.exam_project_id = e.id
       or (d.exam_project_id is null and d.course_id = e.course_id and exists (
         select 1 from public.assessment_documents ad
         where ad.exam_project_id = e.id
           and ad.document_id = p.document_id
           and ad.professor_id = (select auth.uid())
       ))
     )
   order by rank desc
   limit least(greatest(p_match_count, 1), 30);
$$;

revoke execute on function public.search_document_pages(uuid, text, integer) from public, anon;
grant execute on function public.search_document_pages(uuid, text, integer) to authenticated;

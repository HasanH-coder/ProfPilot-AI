-- =============================================================================
-- ProfPilot AI: initial schema
--
--   profiles       one row per professor, created automatically at sign-up
--   courses        courses owned by a professor
--   exam_projects  assessment projects owned by a professor
--
-- Row Level Security (RLS) is enabled on every table, and every policy compares
-- the row's owner with auth.uid() (the signed-in professor). A professor can
-- therefore only read or change their own rows, whatever the browser sends.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Helper: keep updated_at current on every update
-- -----------------------------------------------------------------------------

create function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- Only triggers use this function; API users never need to call it.
revoke execute on function public.set_updated_at() from public, anon, authenticated;


-- -----------------------------------------------------------------------------
-- Tables
-- -----------------------------------------------------------------------------

create table public.profiles (
  id          uuid primary key references auth.users (id) on delete cascade,
  full_name   text,
  institution text,
  department  text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table public.courses (
  id           uuid primary key default gen_random_uuid(),
  -- Defaults to the signed-in professor, so the browser never has to send it.
  professor_id uuid not null default auth.uid()
               references public.profiles (id) on delete cascade,
  code         text not null,
  name         text,
  description  text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create table public.exam_projects (
  id                    uuid primary key default gen_random_uuid(),
  professor_id          uuid not null default auth.uid()
                        references public.profiles (id) on delete cascade,
  course_id             uuid references public.courses (id) on delete set null,
  exam_name             text not null,
  duration_minutes      integer,
  mcq_percentage        integer,
  subjective_percentage integer,
  number_of_versions    integer not null default 1,
  difficulty            text,
  additional_notes      text,
  professor_prompt      text,
  enhanced_prompt       text,
  exam_spec             jsonb,
  generation_mode       text,
  status                text not null default 'draft',
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),

  -- A CHECK passes when its expression is NULL, so optional columns can stay
  -- empty. For example, the 100% total only applies when both percentages are set.
  constraint exam_projects_duration_positive
    check (duration_minutes > 0),
  constraint exam_projects_versions_positive
    check (number_of_versions > 0),
  constraint exam_projects_mcq_percentage_range
    check (mcq_percentage between 0 and 100),
  constraint exam_projects_subjective_percentage_range
    check (subjective_percentage between 0 and 100),
  constraint exam_projects_percentages_total_100
    check (mcq_percentage + subjective_percentage = 100)
);

-- RLS policies filter by these columns on every query, so they need indexes.
create index courses_professor_id_idx on public.courses (professor_id);
create index exam_projects_professor_id_idx on public.exam_projects (professor_id);
create index exam_projects_course_id_idx on public.exam_projects (course_id);

create trigger set_profiles_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

create trigger set_courses_updated_at
  before update on public.courses
  for each row execute function public.set_updated_at();

create trigger set_exam_projects_updated_at
  before update on public.exam_projects
  for each row execute function public.set_updated_at();


-- -----------------------------------------------------------------------------
-- Create a profile automatically when a professor signs up
-- -----------------------------------------------------------------------------

-- security definer: runs with the privileges of the function's owner, so it can
--   insert the profile while the sign-up request is still being processed.
-- set search_path = '': stops anyone from hijacking the function through the
--   search path; every name inside is fully qualified instead.
create function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, full_name)
  values (new.id, nullif(trim(new.raw_user_meta_data ->> 'full_name'), ''));
  return new;
end;
$$;

-- Only the trigger below may run this function, never API users.
revoke execute on function public.handle_new_user() from public, anon, authenticated;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();


-- -----------------------------------------------------------------------------
-- Row Level Security
-- -----------------------------------------------------------------------------

alter table public.profiles enable row level security;
alter table public.courses enable row level security;
alter table public.exam_projects enable row level security;

-- profiles: read and update your own profile. Profiles are created by the
-- trigger above and deleted together with the auth user, so there are no
-- insert or delete policies.

create policy "Professors can view their own profile"
  on public.profiles for select
  to authenticated
  using ((select auth.uid()) = id);

create policy "Professors can update their own profile"
  on public.profiles for update
  to authenticated
  using ((select auth.uid()) = id)
  with check ((select auth.uid()) = id);

-- courses: full control over your own courses only.

create policy "Professors can view their own courses"
  on public.courses for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can create their own courses"
  on public.courses for insert
  to authenticated
  with check ((select auth.uid()) = professor_id);

create policy "Professors can update their own courses"
  on public.courses for update
  to authenticated
  using ((select auth.uid()) = professor_id)
  with check ((select auth.uid()) = professor_id);

create policy "Professors can delete their own courses"
  on public.courses for delete
  to authenticated
  using ((select auth.uid()) = professor_id);

-- exam_projects: full control over your own exam projects only. A project can
-- only be linked to one of your own courses, never to another professor's.

create policy "Professors can view their own exam projects"
  on public.exam_projects for select
  to authenticated
  using ((select auth.uid()) = professor_id);

create policy "Professors can create their own exam projects"
  on public.exam_projects for insert
  to authenticated
  with check (
    (select auth.uid()) = professor_id
    and (
      course_id is null
      or course_id in (
        select courses.id from public.courses
        where courses.professor_id = (select auth.uid())
      )
    )
  );

create policy "Professors can update their own exam projects"
  on public.exam_projects for update
  to authenticated
  using ((select auth.uid()) = professor_id)
  with check (
    (select auth.uid()) = professor_id
    and (
      course_id is null
      or course_id in (
        select courses.id from public.courses
        where courses.professor_id = (select auth.uid())
      )
    )
  );

create policy "Professors can delete their own exam projects"
  on public.exam_projects for delete
  to authenticated
  using ((select auth.uid()) = professor_id);


-- -----------------------------------------------------------------------------
-- Table privileges for the Data API roles
-- -----------------------------------------------------------------------------
-- Privileges decide whether a role may use a table at all; the RLS policies
-- above decide which rows it can use. New Supabase projects no longer grant
-- table access to these roles automatically, so it is granted explicitly here.
-- Signed-out visitors (anon) get no access to any of these tables.

revoke all on public.profiles, public.courses, public.exam_projects
  from anon, authenticated;

grant select, update on public.profiles to authenticated;
grant select, insert, update, delete on public.courses to authenticated;
grant select, insert, update, delete on public.exam_projects to authenticated;

-- service_role is the trusted server-side role (bypasses RLS). It will be used
-- later for backend-only work and must never be exposed to the browser.
grant select, insert, update, delete
  on public.profiles, public.courses, public.exam_projects
  to service_role;

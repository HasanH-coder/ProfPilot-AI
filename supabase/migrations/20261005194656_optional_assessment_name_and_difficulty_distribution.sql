-- =============================================================================
-- ProfPilot AI: optional assessment name, and a difficulty distribution
--
--   exam_name          becomes optional. NULL means the professor hasn't named
--                      the assessment; the app shows "Untitled assessment" in
--                      its place, but never stores that text.
--   easy_percentage,   how much of the assessment should be easy, medium and
--   medium_percentage, hard. Either all three are NULL (not specified) or all
--   hard_percentage    three are set and add up to 100.
--   difficulty         deprecated. Replaced by the three percentages above; kept
--                      only so existing drafts keep their old value.
--
-- course_id was already optional and stays that way.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Optional assessment name
-- -----------------------------------------------------------------------------

alter table public.exam_projects
  alter column exam_name drop not null;

-- An unnamed assessment is NULL, never an empty or blank name.
alter table public.exam_projects
  add constraint exam_projects_exam_name_not_blank
    check (btrim(exam_name) <> '');


-- -----------------------------------------------------------------------------
-- Difficulty distribution
-- -----------------------------------------------------------------------------

alter table public.exam_projects
  add column easy_percentage   smallint,
  add column medium_percentage smallint,
  add column hard_percentage   smallint;

-- As in the initial schema, a CHECK passes when its expression is NULL, so each
-- range check allows an unspecified value.
alter table public.exam_projects
  add constraint exam_projects_easy_percentage_range
    check (easy_percentage between 0 and 100),
  add constraint exam_projects_medium_percentage_range
    check (medium_percentage between 0 and 100),
  add constraint exam_projects_hard_percentage_range
    check (hard_percentage between 0 and 100),
  -- All or nothing: a partly specified distribution such as 30 / NULL / 70 is
  -- rejected. Every branch is written with IS [NOT] NULL, so the expression is
  -- never NULL itself and can't pass by accident.
  add constraint exam_projects_difficulty_distribution_valid
    check (
      (easy_percentage is null and medium_percentage is null and hard_percentage is null)
      or (
        easy_percentage is not null
        and medium_percentage is not null
        and hard_percentage is not null
        and easy_percentage + medium_percentage + hard_percentage = 100
      )
    );

comment on column public.exam_projects.easy_percentage is
  'Share of the assessment that should be easy (0-100). NULL with the other two when not specified.';
comment on column public.exam_projects.medium_percentage is
  'Share of the assessment that should be medium (0-100). NULL with the other two when not specified.';
comment on column public.exam_projects.hard_percentage is
  'Share of the assessment that should be hard (0-100). NULL with the other two when not specified.';


-- -----------------------------------------------------------------------------
-- Legacy single difficulty
-- -----------------------------------------------------------------------------

-- Not dropped: some existing drafts still hold a value here, and dropping the
-- column would lose it. The app no longer reads or writes it, so new
-- assessments leave it NULL. A later migration can remove it, or convert the
-- old values, once that is decided.
comment on column public.exam_projects.difficulty is
  'Deprecated: replaced by easy_percentage, medium_percentage and hard_percentage. No longer read or written by the app.';

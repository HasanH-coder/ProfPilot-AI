-- =============================================================================
-- ProfPilot AI: Exam Learning Focus (Bloom's taxonomy)
--
--   exam_projects.learning_focus      how the professor chose the cognitive mix:
--                                     'automatic' (ProfPilot recommends it, the
--                                     default), a preset ('balanced',
--                                     'practical', 'critical'), or 'custom'.
--   exam_projects.bloom_distribution  the professor's own split by marks, only
--                                     for 'custom': {"remember": 10, ...}, six
--                                     whole numbers that add up to 100.
--   exam_questions.cognitive_level    the Bloom's level a question assesses.
--                                     NULL for questions written before levels
--                                     were recorded.
--
-- Presets are resolved in the app (backend/app/domain/setup.py), so only the
-- professor's choice is stored, never percentages they didn't enter.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Learning focus on the setup
-- -----------------------------------------------------------------------------

-- Six levels, each a whole number from 0 to 100, adding up to 100. A function,
-- because a CHECK can't contain a subquery. CASE makes sure a value is only
-- cast once it is known to be a number.
create function public.is_bloom_distribution(distribution jsonb)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select jsonb_typeof(distribution) = 'object'
     and distribution - array['remember', 'understand', 'apply', 'analyze', 'evaluate', 'create'] = '{}'::jsonb
     and (
       select count(*) = 6
          and bool_and(
                case when jsonb_typeof(item.value) = 'number'
                     then item.value::numeric between 0 and 100 and item.value::numeric % 1 = 0
                     else false
                end)
          and sum(case when jsonb_typeof(item.value) = 'number' then item.value::numeric end) = 100
         from jsonb_each(distribution) as item
     );
$$;

alter table public.exam_projects
  add column learning_focus     text not null default 'automatic',
  add column bloom_distribution jsonb,
  add constraint exam_projects_learning_focus_valid
    check (learning_focus in ('automatic', 'balanced', 'practical', 'critical', 'custom')),
  -- Written with IS [NOT] NULL and coalesce, so the expression is never NULL
  -- itself and can't pass by accident.
  add constraint exam_projects_bloom_distribution_valid
    check (
      (learning_focus <> 'custom' and bloom_distribution is null)
      or (
        learning_focus = 'custom'
        and bloom_distribution is not null
        and coalesce(public.is_bloom_distribution(bloom_distribution), false)
      )
    );

comment on column public.exam_projects.learning_focus is
  'How the cognitive (Bloom''s) mix is chosen: automatic, balanced, practical, critical or custom.';
comment on column public.exam_projects.bloom_distribution is
  'Custom learning focus only: share of marks per Bloom''s level, six whole numbers adding up to 100.';


-- -----------------------------------------------------------------------------
-- The plan goes stale when the learning focus changes, like any other setting
-- -----------------------------------------------------------------------------

create or replace function public.mark_exam_spec_stale()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if old.spec_status in ('draft', 'approved')
     and new.spec_status = old.spec_status
     and (new.course_id, new.exam_name, new.duration_minutes, new.mcq_percentage,
          new.subjective_percentage, new.number_of_versions, new.easy_percentage,
          new.medium_percentage, new.hard_percentage, new.learning_focus,
          new.bloom_distribution, new.additional_notes, new.professor_prompt)
         is distinct from
         (old.course_id, old.exam_name, old.duration_minutes, old.mcq_percentage,
          old.subjective_percentage, old.number_of_versions, old.easy_percentage,
          old.medium_percentage, old.hard_percentage, old.learning_focus,
          old.bloom_distribution, old.additional_notes, old.professor_prompt)
  then
    new.spec_status := 'stale';
  end if;
  return new;
end;
$$;


-- -----------------------------------------------------------------------------
-- The level each question assesses
-- -----------------------------------------------------------------------------

alter table public.exam_questions
  add column cognitive_level text,
  add constraint exam_questions_cognitive_level_valid
    check (cognitive_level in ('remember', 'understand', 'apply', 'analyze', 'evaluate', 'create'));

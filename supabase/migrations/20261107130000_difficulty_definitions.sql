-- =============================================================================
-- ProfPilot AI: the professor's own definitions of easy, medium and hard
--
--   exam_projects.difficulty_definitions  NULL means ProfPilot's defaults
--                                         (backend/app/domain/setup.py). Otherwise
--                                         {"easy": "...", "medium": "...", "hard": "..."}:
--                                         all three, each a non-empty text of at
--                                         most 400 characters.
-- =============================================================================

alter table public.exam_projects
  add column difficulty_definitions jsonb,
  -- Written with IS [NOT] NULL and coalesce, so the expression is never NULL itself.
  add constraint exam_projects_difficulty_definitions_valid
    check (
      difficulty_definitions is null
      or (
        jsonb_typeof(difficulty_definitions) = 'object'
        and difficulty_definitions - array['easy', 'medium', 'hard'] = '{}'::jsonb
        and coalesce(jsonb_typeof(difficulty_definitions -> 'easy') = 'string', false)
        and coalesce(jsonb_typeof(difficulty_definitions -> 'medium') = 'string', false)
        and coalesce(jsonb_typeof(difficulty_definitions -> 'hard') = 'string', false)
        and coalesce(length(btrim(difficulty_definitions ->> 'easy')) between 1 and 400, false)
        and coalesce(length(btrim(difficulty_definitions ->> 'medium')) between 1 and 400, false)
        and coalesce(length(btrim(difficulty_definitions ->> 'hard')) between 1 and 400, false)
      )
    );

comment on column public.exam_projects.difficulty_definitions is
  'The professor''s own meaning of easy, medium and hard; NULL uses ProfPilot''s defaults.';


-- -----------------------------------------------------------------------------
-- The plan goes stale when the definitions change, like any other setting
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
          new.medium_percentage, new.hard_percentage, new.difficulty_definitions,
          new.learning_focus, new.bloom_distribution, new.additional_notes,
          new.professor_prompt)
         is distinct from
         (old.course_id, old.exam_name, old.duration_minutes, old.mcq_percentage,
          old.subjective_percentage, old.number_of_versions, old.easy_percentage,
          old.medium_percentage, old.hard_percentage, old.difficulty_definitions,
          old.learning_focus, old.bloom_distribution, old.additional_notes,
          old.professor_prompt)
  then
    new.spec_status := 'stale';
  end if;
  return new;
end;
$$;

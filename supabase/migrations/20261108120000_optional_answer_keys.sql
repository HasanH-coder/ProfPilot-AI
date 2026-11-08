-- Questions are written first and their answer keys only when the professor asks, so a
-- multiple-choice question may have no correct answer yet. One it has must still be one of
-- its choices, and any other question still has neither choices nor a correct answer.
alter table public.exam_questions drop constraint exam_questions_choices_shape;

alter table public.exam_questions add constraint exam_questions_choices_shape check (
  case
    when type = 'mcq' then
      choices is not null
      and jsonb_typeof(choices) = 'array'
      and jsonb_array_length(choices) between 2 and 8
      and (
        correct_choice is null
        or choices @> jsonb_build_array(jsonb_build_object('id', correct_choice))
      )
    else choices is null and correct_choice is null
  end
);

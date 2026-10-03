"use server";

import type { PostgrestError } from "@supabase/supabase-js";
import { refresh } from "next/cache";

import { getCurrentProfessor } from "@/lib/auth/current-professor";
import type { Course } from "@/lib/courses/queries";
import { createClient } from "@/lib/supabase/server";

export type CourseFormState = {
  /** A problem with the whole form, such as the database being unreachable. */
  error?: string;
  /** Problems with single fields, shown under each input. */
  fieldErrors?: { code?: string; name?: string };
  /** The saved course, once the form succeeds. */
  course?: Course;
};

const MAX_CODE_LENGTH = 30;
const MAX_NAME_LENGTH = 120;

// Every action checks the session itself: Server Actions can be called
// directly, not only from our pages. Row Level Security then makes sure a
// professor can only ever touch their own courses.

export async function createCourse(
  _state: CourseFormState,
  formData: FormData,
): Promise<CourseFormState> {
  await getCurrentProfessor();
  const { values, fieldErrors } = readCourseForm(formData);
  if (!values) return { fieldErrors };

  const supabase = await createClient();
  // professor_id is filled in by the database with the signed-in professor.
  const { data, error } = await supabase
    .from("courses")
    .insert(values)
    .select("id, code, name")
    .single();
  if (error) return failure("create", error);

  // Re-render the current page so it shows the new course.
  refresh();
  return { course: data };
}

export async function updateCourse(
  courseId: string,
  _state: CourseFormState,
  formData: FormData,
): Promise<CourseFormState> {
  await getCurrentProfessor();
  const { values, fieldErrors } = readCourseForm(formData);
  if (!values) return { fieldErrors };

  const supabase = await createClient();
  const { data, error } = await supabase
    .from("courses")
    .update(values)
    .eq("id", courseId)
    .select("id, code, name")
    .maybeSingle();
  if (error) return failure("update", error);
  if (!data) return { error: "This course no longer exists." };

  refresh();
  return { course: data };
}

export async function deleteCourse(courseId: string): Promise<{ error?: string }> {
  await getCurrentProfessor();

  const supabase = await createClient();
  // Assessments linked to this course are kept; the database unlinks them.
  const { data, error } = await supabase
    .from("courses")
    .delete()
    .eq("id", courseId)
    .select("id");
  if (error) return failure("delete", error);
  if (data.length === 0) return { error: "This course no longer exists." };

  refresh();
  return {};
}

function readCourseForm(formData: FormData) {
  const code = getText(formData, "code");
  const name = getText(formData, "name");

  const fieldErrors: CourseFormState["fieldErrors"] = {};
  if (!code) fieldErrors.code = "Enter a course code.";
  else if (code.length > MAX_CODE_LENGTH) fieldErrors.code = `Use ${MAX_CODE_LENGTH} characters or fewer.`;
  if (name.length > MAX_NAME_LENGTH) fieldErrors.name = `Use ${MAX_NAME_LENGTH} characters or fewer.`;

  if (fieldErrors.code || fieldErrors.name) return { fieldErrors };
  return { values: { code, name: name || null } };
}

function getText(formData: FormData, name: string) {
  const value = formData.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function failure(action: string, error: PostgrestError) {
  console.error(`Could not ${action} course:`, error);
  return { error: `The course couldn't be ${action}d. Please try again.` };
}

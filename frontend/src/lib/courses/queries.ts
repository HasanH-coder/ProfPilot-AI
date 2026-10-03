import "server-only";

import { createClient } from "@/lib/supabase/server";

/** A course as shown in lists and pickers. */
export type Course = {
  id: string;
  code: string;
  name: string | null;
};

/** The signed-in professor's courses, sorted by code. Row Level Security returns only their own. */
export async function getCourses(): Promise<Course[]> {
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("courses")
    .select("id, code, name")
    .order("code");
  if (error) throw new Error(`Could not load courses: ${error.message}`);
  return data;
}

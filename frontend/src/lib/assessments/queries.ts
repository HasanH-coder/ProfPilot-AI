import "server-only";

import type { Difficulty } from "@/lib/assessments/draft";
import { createClient } from "@/lib/supabase/server";

/** An assessment as shown in the Assessments list. */
export type AssessmentListItem = {
  id: string;
  examName: string;
  status: string;
  createdAt: string;
  courseCode: string | null;
  durationMinutes: number | null;
  numberOfVersions: number;
  difficulty: Difficulty | null;
};

/** The signed-in professor's assessments, newest first. Row Level Security returns only their own. */
export async function getAssessments(): Promise<AssessmentListItem[]> {
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("exam_projects")
    .select(
      "id, exam_name, status, created_at, duration_minutes, number_of_versions, difficulty, course:courses(code)",
    )
    .order("created_at", { ascending: false });
  if (error) throw new Error(`Could not load assessments: ${error.message}`);

  return data.map((row) => ({
    id: row.id,
    examName: row.exam_name,
    status: row.status,
    createdAt: row.created_at,
    courseCode: row.course?.code ?? null,
    durationMinutes: row.duration_minutes,
    numberOfVersions: row.number_of_versions,
    difficulty: row.difficulty as Difficulty | null,
  }));
}

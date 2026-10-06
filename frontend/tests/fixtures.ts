// Test data shaped like the AI API's replies (see src/lib/api/types.ts).

import type { BuilderMessage, Exam, Question } from "@/lib/api/types";
import type { Professor } from "@/lib/auth/current-professor";

export const professor: Professor = {
  id: "11111111-1111-4111-8111-111111111111",
  email: "prof@aub.edu.lb",
  fullName: "Test Professor",
  institution: "AUB",
};

export const courses = [{ id: "course-1", code: "CMPS 297U", name: "Machine Learning" }];

const PROMPT =
  "A logistics company records delivery times for 40 routes over two weeks. The data are skewed by a few very late deliveries.\n\nExplain which measure of central tendency best summarizes the typical delivery time.";

export function makeQuestion(index: number): Question {
  const type = (["mcq", "short_answer", "problem"] as const)[index % 3];
  return {
    id: `question-${index + 1}`,
    examId: "exam-1",
    versionId: "version-a",
    sectionId: "section-1",
    slotId: `slot-${index + 1}`,
    number: index + 1,
    position: index + 1,
    type,
    difficulty: (["medium", "easy", "hard"] as const)[index % 3],
    points: type === "mcq" ? 2 : 10,
    prompt: `${PROMPT} (Question ${index + 1})`,
    choices: type === "mcq" ? ["A", "B", "C", "D"].map((id) => ({ id, text: `Option ${id}` })) : null,
    correctChoice: type === "mcq" ? "B" : null,
    subparts: null,
    answer: "The median, because it resists outliers.",
    solution: "The distribution is right-skewed, so the median is the better summary.",
    rubric: type === "mcq" ? null : [{ criterion: "Method", points: 5 }, { criterion: "Result", points: 5 }],
    explanation: null,
    concepts: [`concept ${index + 1}`],
    learningObjectives: [],
    sources: [],
    figureDocumentId: null,
    estimatedMinutes: type === "mcq" ? 2 : 8,
    status: "draft",
    needsSolutionReview: false,
    revisionCount: 0,
    updatedAt: "2026-10-06T10:00:00Z",
  };
}

export function makeExam(questionCount: number): Exam {
  const summary = {
    questionCount,
    totalPoints: 0,
    estimatedMinutes: 0,
    mcqPercent: 0,
    subjectivePercent: 0,
    easyPercent: 0,
    mediumPercent: 0,
    hardPercent: 0,
  };
  return {
    id: "exam-1",
    assessmentId: "assessment-1",
    mode: "interactive",
    status: "building",
    errorMessage: null,
    reviewStatus: "not_run",
    review: null,
    reviewedAt: null,
    finalizedAt: null,
    createdAt: "2026-10-06T10:00:00Z",
    updatedAt: "2026-10-06T10:00:00Z",
    spec: null,
    plannedQuestionCount: 10,
    versions: [{ id: "version-a", label: "A", position: 1, summary, warnings: [] }],
    sections: [{ id: "section-1", position: 1, title: "Questions", instructions: null }],
    questions: Array.from({ length: questionCount }, (_, index) => makeQuestion(index)),
  };
}

export function makeMessages(count: number): BuilderMessage[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `message-${index}`,
    role: index % 2 ? "assistant" : "professor",
    channel: "text",
    content: index % 2 ? `Here's question ${index}. It tests the median of skewed data.` : "Generate the next question.",
    createdAt: "2026-10-06T10:00:00Z",
  }));
}

/** A promise the test resolves when it chooses (e.g. the AI "finishing"). */
export function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

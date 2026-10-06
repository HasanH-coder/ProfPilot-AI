"use client";

import { Plus, Trash2 } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { DIFFICULTY_LABELS, TYPE_LABELS } from "@/components/exam/labels";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { apiRequest, errorMessage } from "@/lib/api/client";
import type { Choice, Question, QuestionDifficulty, QuestionResult, QuestionType, RubricItem } from "@/lib/api/types";

type EditState = {
  prompt: string;
  type: QuestionType;
  difficulty: QuestionDifficulty;
  points: string;
  choices: Choice[];
  correctChoice: string | null;
  subparts: { label: string; prompt: string; points: string; answer: string }[];
  answer: string;
  solution: string;
  explanation: string;
  rubric: { criterion: string; points: string }[];
};

const LETTERS = "ABCDEFGH";

function fromQuestion(question: Question): EditState {
  return {
    prompt: question.prompt,
    type: question.type,
    difficulty: question.difficulty,
    points: String(question.points),
    choices: question.choices ?? [],
    correctChoice: question.correctChoice,
    subparts: (question.subparts ?? []).map((part) => ({
      label: part.label,
      prompt: part.prompt,
      points: String(part.points),
      answer: part.answer,
    })),
    answer: question.answer ?? "",
    solution: question.solution ?? "",
    explanation: question.explanation ?? "",
    rubric: (question.rubric ?? []).map((item) => ({ criterion: item.criterion, points: String(item.points) })),
  };
}

type QuestionEditDialogProps = {
  question: Question;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (result: QuestionResult) => void;
};

/** Edit any part of a question by hand. Only the fields that changed are sent. */
export function QuestionEditDialog({ question, open, onOpenChange, onSaved }: QuestionEditDialogProps) {
  const [state, setState] = useState<EditState>(() => fromQuestion(question));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ids = { prompt: useId(), type: useId(), difficulty: useId(), points: useId(), answer: useId(), solution: useId(), explanation: useId() };

  function reset(next: boolean) {
    if (next) {
      setState(fromQuestion(question));
      setError(null);
    }
    onOpenChange(next);
  }

  function set<K extends keyof EditState>(key: K, value: EditState[K]) {
    setState((current) => ({ ...current, [key]: value }));
  }

  function changeType(type: QuestionType) {
    setState((current) => {
      if (type === "mcq" && current.choices.length === 0) {
        const choices = ["A", "B", "C", "D"].map((id) => ({ id, text: "" }));
        return { ...current, type, choices, correctChoice: "A", subparts: [] };
      }
      return { ...current, type };
    });
  }

  function buildChanges(): Record<string, unknown> | string {
    const original = fromQuestion(question);
    const points = Number(state.points);
    if (!Number.isFinite(points) || points <= 0 || points > 1000) return "Enter the marks as a number greater than 0.";
    const changes: Record<string, unknown> = {};
    if (state.prompt.trim() !== original.prompt.trim()) changes.prompt = state.prompt.trim();
    if (state.type !== original.type) changes.type = state.type;
    if (state.difficulty !== original.difficulty) changes.difficulty = state.difficulty;
    if (points !== question.points) changes.points = points;

    if (state.type === "mcq") {
      const choices = state.choices.map((choice) => ({ id: choice.id, text: choice.text.trim() }));
      if (choices.length < 2 || choices.some((choice) => !choice.text)) return "Give every choice some text (at least 2 choices).";
      if (!state.correctChoice || !choices.some((choice) => choice.id === state.correctChoice)) return "Choose the correct answer.";
      if (JSON.stringify(choices) !== JSON.stringify(original.choices) || state.type !== original.type) changes.choices = choices;
      if (state.correctChoice !== original.correctChoice || state.type !== original.type) changes.correctChoice = state.correctChoice;
    } else if (original.type === "mcq") {
      changes.choices = null;
      changes.correctChoice = null;
    }

    if (state.type !== "mcq" && JSON.stringify(state.subparts) !== JSON.stringify(original.subparts)) {
      const parts = [];
      for (const part of state.subparts) {
        const partPoints = Number(part.points);
        if (!part.prompt.trim() || !Number.isFinite(partPoints) || partPoints <= 0) {
          return "Each part needs a question and marks greater than 0.";
        }
        const previous = question.subparts?.find((item) => item.label === part.label);
        parts.push({
          label: part.label,
          prompt: part.prompt.trim(),
          points: partPoints,
          answer: part.answer.trim(),
          rubric: previous && previous.points === partPoints ? previous.rubric : [{ criterion: "Correct answer", points: partPoints }],
        });
      }
      changes.subparts = parts.length > 0 ? parts : null;
    }

    if (state.answer.trim() !== original.answer.trim()) changes.answer = state.answer.trim();
    if (state.solution.trim() !== original.solution.trim()) changes.solution = state.solution.trim();
    if (state.explanation.trim() !== original.explanation.trim()) changes.explanation = state.explanation.trim() || null;
    if (JSON.stringify(state.rubric) !== JSON.stringify(original.rubric)) {
      const rubric: RubricItem[] = [];
      for (const item of state.rubric) {
        const itemPoints = Number(item.points);
        if (!item.criterion.trim() || !Number.isFinite(itemPoints) || itemPoints < 0) return "Each rubric line needs a criterion and marks.";
        rubric.push({ criterion: item.criterion.trim(), points: itemPoints });
      }
      changes.rubric = rubric.length > 0 ? rubric : null;
    }
    return changes;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    event.stopPropagation();
    const changes = buildChanges();
    if (typeof changes === "string") {
      setError(changes);
      return;
    }
    if (Object.keys(changes).length === 0) {
      onOpenChange(false);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const result = await apiRequest<QuestionResult>(`/api/questions/${question.id}`, { method: "PATCH", body: changes });
      onSaved(result);
      onOpenChange(false);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  const rubricTotal = state.rubric.reduce((total, item) => total + (Number(item.points) || 0), 0);

  return (
    <Dialog open={open} onOpenChange={reset}>
      <DialogContent className="workspace-theme max-h-[90svh] overflow-y-auto sm:max-w-2xl">
        <form onSubmit={handleSubmit} className="flex flex-col gap-5" noValidate>
          <DialogHeader>
            <DialogTitle>Edit question {question.number}</DialogTitle>
            <DialogDescription>
              If you change the question but not its answer key, it&apos;s flagged so you can check or regenerate the key.
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-2">
            <Label htmlFor={ids.prompt}>Question</Label>
            <Textarea id={ids.prompt} className="min-h-28" value={state.prompt} onChange={(event) => set("prompt", event.target.value)} />
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <div className="flex flex-col gap-2">
              <Label htmlFor={ids.type}>Type</Label>
              <Select items={TYPE_LABELS} value={state.type} onValueChange={(value) => value && changeType(value as QuestionType)}>
                <SelectTrigger id={ids.type} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="workspace-theme">
                  {Object.entries(TYPE_LABELS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor={ids.difficulty}>Difficulty</Label>
              <Select
                items={DIFFICULTY_LABELS}
                value={state.difficulty}
                onValueChange={(value) => value && set("difficulty", value as QuestionDifficulty)}
              >
                <SelectTrigger id={ids.difficulty} className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="workspace-theme">
                  {Object.entries(DIFFICULTY_LABELS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor={ids.points}>Marks</Label>
              <Input
                id={ids.points}
                type="number"
                inputMode="decimal"
                min={0.5}
                step={0.5}
                value={state.points}
                onChange={(event) => set("points", event.target.value)}
              />
            </div>
          </div>

          {state.type === "mcq" ? (
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-2 text-sm font-medium">Choices (select the correct one)</legend>
              {state.choices.map((choice, index) => (
                <div key={choice.id} className="flex items-center gap-2">
                  <input
                    type="radio"
                    name={`correct-${question.id}`}
                    aria-label={`Choice ${choice.id} is correct`}
                    checked={state.correctChoice === choice.id}
                    onChange={() => set("correctChoice", choice.id)}
                    className="size-4 accent-primary"
                  />
                  <span className="w-5 text-sm font-medium">{choice.id}.</span>
                  <Input
                    aria-label={`Choice ${choice.id}`}
                    value={choice.text}
                    onChange={(event) =>
                      set(
                        "choices",
                        state.choices.map((item, position) => (position === index ? { ...item, text: event.target.value } : item)),
                      )
                    }
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Remove choice ${choice.id}`}
                    disabled={state.choices.length <= 2}
                    onClick={() => {
                      const remaining = state.choices.filter((_, position) => position !== index);
                      const relabeled = remaining.map((item, position) => ({ ...item, id: LETTERS[position] }));
                      const correctIndex = state.choices.findIndex((item) => item.id === state.correctChoice);
                      const nextCorrect =
                        correctIndex === index ? null : relabeled[correctIndex > index ? correctIndex - 1 : correctIndex]?.id ?? null;
                      setState((current) => ({ ...current, choices: relabeled, correctChoice: nextCorrect }));
                    }}
                  >
                    <Trash2 />
                  </Button>
                </div>
              ))}
              {state.choices.length < 8 && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="w-fit"
                  onClick={() => set("choices", [...state.choices, { id: LETTERS[state.choices.length], text: "" }])}
                >
                  <Plus />
                  Add choice
                </Button>
              )}
            </fieldset>
          ) : (
            <fieldset className="flex flex-col gap-3">
              <legend className="mb-2 text-sm font-medium">Parts (optional)</legend>
              {state.subparts.map((part, index) => (
                <div key={index} className="grid gap-2 rounded-lg border p-3 sm:grid-cols-[3rem_minmax(0,1fr)_6rem_auto]">
                  <Input
                    aria-label={`Part ${index + 1} label`}
                    value={part.label}
                    onChange={(event) =>
                      set("subparts", state.subparts.map((item, position) => (position === index ? { ...item, label: event.target.value } : item)))
                    }
                  />
                  <Input
                    aria-label={`Part ${part.label} question`}
                    value={part.prompt}
                    onChange={(event) =>
                      set("subparts", state.subparts.map((item, position) => (position === index ? { ...item, prompt: event.target.value } : item)))
                    }
                  />
                  <Input
                    aria-label={`Part ${part.label} marks`}
                    type="number"
                    min={0.5}
                    step={0.5}
                    value={part.points}
                    onChange={(event) =>
                      set("subparts", state.subparts.map((item, position) => (position === index ? { ...item, points: event.target.value } : item)))
                    }
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Remove part ${part.label}`}
                    onClick={() => set("subparts", state.subparts.filter((_, position) => position !== index))}
                  >
                    <Trash2 />
                  </Button>
                  <Textarea
                    aria-label={`Part ${part.label} answer`}
                    placeholder="Answer for this part"
                    className="min-h-16 sm:col-span-4"
                    value={part.answer}
                    onChange={(event) =>
                      set("subparts", state.subparts.map((item, position) => (position === index ? { ...item, answer: event.target.value } : item)))
                    }
                  />
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-fit"
                onClick={() =>
                  set("subparts", [
                    ...state.subparts,
                    { label: "abcdefghij"[state.subparts.length] ?? String(state.subparts.length + 1), prompt: "", points: "1", answer: "" },
                  ])
                }
              >
                <Plus />
                Add part
              </Button>
            </fieldset>
          )}

          <div className="flex flex-col gap-2">
            <Label htmlFor={ids.answer}>Answer</Label>
            <Textarea id={ids.answer} className="min-h-16" value={state.answer} onChange={(event) => set("answer", event.target.value)} />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor={ids.solution}>Worked solution</Label>
            <Textarea id={ids.solution} className="min-h-24" value={state.solution} onChange={(event) => set("solution", event.target.value)} />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor={ids.explanation}>{state.type === "mcq" ? "Why the other choices are wrong" : "Notes for markers"}</Label>
            <Textarea id={ids.explanation} className="min-h-16" value={state.explanation} onChange={(event) => set("explanation", event.target.value)} />
          </div>

          {state.type !== "mcq" && (
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-2 text-sm font-medium">
                Marking rubric <span className="font-normal text-muted-foreground">(total {rubricTotal} of {state.points || 0})</span>
              </legend>
              {state.rubric.map((item, index) => (
                <div key={index} className="flex items-center gap-2">
                  <Input
                    aria-label={`Rubric criterion ${index + 1}`}
                    value={item.criterion}
                    onChange={(event) =>
                      set("rubric", state.rubric.map((row, position) => (position === index ? { ...row, criterion: event.target.value } : row)))
                    }
                  />
                  <Input
                    aria-label={`Rubric marks ${index + 1}`}
                    type="number"
                    min={0}
                    step={0.5}
                    className="w-24"
                    value={item.points}
                    onChange={(event) =>
                      set("rubric", state.rubric.map((row, position) => (position === index ? { ...row, points: event.target.value } : row)))
                    }
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Remove rubric line ${index + 1}`}
                    onClick={() => set("rubric", state.rubric.filter((_, position) => position !== index))}
                  >
                    <Trash2 />
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-fit"
                onClick={() => set("rubric", [...state.rubric, { criterion: "", points: "1" }])}
              >
                <Plus />
                Add rubric line
              </Button>
            </fieldset>
          )}

          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => reset(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={saving}>
              {saving && <Spinner />}
              Save changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

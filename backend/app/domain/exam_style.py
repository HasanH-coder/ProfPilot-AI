"""The professor's previous exams in numbers, counted in code from their analysed questions.

The model labels each previous question (what students do, its type, difficulty, length, case); code
counts them. The planner and the writers then match the old exam's proportions (how many questions make
students calculate, how long a question is, how many sit under a case) instead of a vague impression,
and the plan is checked against them.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass
from statistics import median

from app.schemas.ai import PlannedQuestion, QuestionContent, StyleProfile

TASKS = ("conceptual", "application", "calculation")
_TASK_NAMES = {"conceptual": "conceptual", "application": "application", "calculation": "calculation"}
_TYPE_NAMES = {"mcq": "multiple-choice", "short_answer": "short-answer", "long_answer": "long-answer", "problem": "problem"}

# Fewer analysed questions than this say too little to count proportions from.
_MIN_QUESTIONS = 3
# A type's typical length is counted from at least this many of its questions.
_MIN_OF_TYPE = 3


@dataclass(frozen=True)
class StyleNumbers:
    exams: int
    questions: int
    tasks: dict[str, int]
    types: dict[str, int]
    difficulties: dict[str, int]
    # Median words of a question with its choices, by type (only types with enough questions).
    words: dict[str, int]
    easy_words: int | None
    hard_words: int | None
    # Questions under each shared case.
    cases: list[int]
    topics: list[tuple[str, int]]

    def share(self, task: str) -> float:
        return self.tasks.get(task, 0) / self.questions

    def expected(self, task: str, count: int) -> float:
        return self.share(task) * count

    def most_calculations(self, count: int) -> int:
        """The most calculation questions that still match the previous exams for `count` questions."""
        return math.floor(self.expected("calculation", count) + max(1.0, 0.05 * count))

    @property
    def per_exam(self) -> int:
        return max(1, round(self.questions / max(1, self.exams)))


def style_numbers(style: StyleProfile | None) -> StyleNumbers | None:
    """The previous exams in numbers, or None when they weren't analysed question by question."""
    questions = (style.questions or []) if style else []
    if len(questions) < _MIN_QUESTIONS:
        return None
    by_type: dict[str, list[int]] = {}
    for question in questions:
        by_type.setdefault(question.type, []).append(max(0, question.words))
    by_difficulty: dict[str, list[int]] = {}
    for question in questions:
        by_difficulty.setdefault(question.difficulty, []).append(max(0, question.words))
    cases = Counter((question.exam, question.case.strip().lower()) for question in questions if (question.case or "").strip())
    topics = Counter(" ".join(question.topic.split()) for question in questions if question.topic.strip())
    return StyleNumbers(
        exams=max(1, len({question.exam for question in questions}), style.exams_analyzed if style else 1),
        questions=len(questions),
        tasks={task: sum(1 for question in questions if question.task == task) for task in TASKS},
        types=dict(Counter(question.type for question in questions)),
        difficulties=dict(Counter(question.difficulty for question in questions)),
        words={kind: round(median(counts)) for kind, counts in by_type.items() if len(counts) >= _MIN_OF_TYPE},
        easy_words=round(median(by_difficulty["easy"])) if len(by_difficulty.get("easy", [])) >= 2 else None,
        hard_words=round(median(by_difficulty["hard"])) if len(by_difficulty.get("hard", [])) >= 2 else None,
        cases=sorted(cases.values(), reverse=True),
        topics=topics.most_common(8),
    )


def _percent(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "0%"


def render_style_numbers(numbers: StyleNumbers | None, planned: int | None = None) -> str:
    """The previous exams in numbers, for the planner and the writers. Empty without them."""
    if numbers is None:
        return ""
    n = numbers.questions
    lines = [
        f"- Questions per exam: about {numbers.per_exam}"
        + (f" ({numbers.exams} exams, {n} questions in all)." if numbers.exams > 1 else "."),
        "- What students do: "
        + ", ".join(f"{numbers.tasks[task]} {_TASK_NAMES[task]} ({_percent(numbers.tasks[task], n)})" for task in TASKS)
        + ". (Conceptual: explain or tell apart ideas. Application: use an idea on a given situation or given "
        "results without computing. Calculation: students compute any number.)",
        "- Question types: "
        + ", ".join(
            f"{count} {_TYPE_NAMES.get(kind, kind)}" for kind, count in sorted(numbers.types.items(), key=lambda i: -i[1])
        )
        + ".",
        "- Difficulty: "
        + ", ".join(f"{numbers.difficulties.get(level, 0)} {level}" for level in ("easy", "medium", "hard"))
        + ".",
    ]
    if numbers.words:
        lengths = "; ".join(
            f"a {_TYPE_NAMES.get(kind, kind)} question about {words} words" for kind, words in numbers.words.items()
        )
        line = f"- Length (the question with its choices, not the case it builds on): {lengths}."
        if numbers.easy_words and numbers.hard_words:
            line += (
                f" Their hard questions run to about {numbers.hard_words} words and their easy ones about "
                f"{numbers.easy_words}: "
                + (
                    "a hard question is no longer than an easy one; it is harder through its thinking."
                    if numbers.hard_words <= numbers.easy_words * 1.25
                    else "keep any extra length to what the harder thinking really needs."
                )
            )
        lines.append(line)
    if numbers.cases:
        in_cases = sum(numbers.cases)
        lines.append(
            f"- Cases: {len(numbers.cases)} shared case(s), with "
            + ", ".join(str(size) for size in numbers.cases)
            + f" questions; {n - in_cases} questions stand alone."
        )
    else:
        lines.append("- Cases: none; every question stands alone.")
    if numbers.topics:
        lines.append("- Topics asked most: " + "; ".join(f"{topic} ({count})" for topic, count in numbers.topics) + ".")
    count = planned or numbers.per_exam
    expected = numbers.expected("calculation", count)
    lines.append(
        f"Match these proportions, scaled to this exam's number of questions: for {count} questions that is about "
        f"{round(expected)} calculation question(s), never more than {numbers.most_calculations(count)}, and about "
        f"{round(numbers.expected('conceptual', count))} conceptual and {round(numbers.expected('application', count))} "
        "application questions. The professor's own instructions come first."
    )
    return "THE PREVIOUS EXAMS IN NUMBERS (counted from their questions)\n" + "\n".join(lines)


def mix_problems(questions: list[PlannedQuestion], numbers: StyleNumbers | None) -> list[str]:
    """Softer plan problems: a mix of tasks far from the previous exams' (above all, more calculation)."""
    if numbers is None or not questions:
        return []
    count = len(questions)
    missing = [q.number for q in questions if q.task is None]
    if missing:
        return [
            "Set task (conceptual, application or calculation) for every question; it is missing for question(s) "
            + ", ".join(map(str, missing[:10]))
            + "."
        ]
    planned = Counter(q.task for q in questions)
    problems: list[str] = []
    most = numbers.most_calculations(count)
    previous = (
        f"{numbers.tasks['conceptual']} conceptual, {numbers.tasks['application']} application and "
        f"{numbers.tasks['calculation']} calculation in {numbers.questions}"
    )
    if planned["calculation"] > most:
        problems.append(
            f"{planned['calculation']} of the {count} questions make students calculate, but the professor's previous "
            f"exams have {previous}: plan at most {most} calculation question(s) here. Turn the others into "
            "conceptual or application questions on the same topics (give any numbers already computed and ask what "
            "they mean), unless the professor's instructions ask for more calculation."
        )
    off = [
        task
        for task in ("conceptual", "application")
        if abs(planned[task] / count - numbers.share(task)) > 0.2 and abs(planned[task] - numbers.expected(task, count)) >= 2
    ]
    if off:
        problems.append(
            f"The plan has {planned['conceptual']} conceptual, {planned['application']} application and "
            f"{planned['calculation']} calculation questions; the previous exams have {previous}. Bring the mix "
            "closer to theirs, scaled to this exam's number of questions, unless the professor asked otherwise."
        )
    return problems


# Lines that are a table, code or display mathematics: data to read, not words of the question.
_NOT_PROSE = re.compile(r"^\s*(\||```|\\\[|\\\])")


def question_words(content: QuestionContent) -> int:
    """Words students read in a question: its text, its parts and its choices (not tables, code or the case)."""
    texts = [
        content.prompt,
        *(choice.text for choice in content.choices or []),
        *(part.prompt for part in content.subparts or []),
    ]
    words = 0
    for text in texts:
        fenced = False
        for line in (text or "").splitlines():
            if line.strip().startswith("```"):
                fenced = not fenced
                continue
            if fenced or _NOT_PROSE.match(line):
                continue
            words += len(line.split())
    return words


def length_problems(content: QuestionContent, label: str, numbers: StyleNumbers | None) -> list[str]:
    """A question far longer than the professor's previous questions of its type."""
    if numbers is None or content.type not in numbers.words:
        return []
    typical = numbers.words[content.type]
    limit = max(round(2.5 * typical), typical + 50)
    words = question_words(content)
    if words <= limit:
        return []
    return [
        f"{label} runs to {words} words; the professor's previous exams ask a {_TYPE_NAMES.get(content.type, content.type)} "
        f"question in about {typical}. Cut it to what students need (at most {limit} words): a harder question is "
        "harder through its thinking, never through more words, a longer setting, more data or extra steps."
    ]

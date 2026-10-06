"""The final review before export: what's complete, what needs attention, what blocks.

Three levels:

- blocked: export can't work yet (generation still running);
- serious: export only after the professor confirms (an MCQ without a valid
  answer, a question without an answer key, an incomplete generation, versions
  that don't match);
- warning / info: worth knowing, never blocking (unapproved questions, splits a
  little off target, the quality check not run).
"""

from typing import Any

from app.domain import distribution
from app.services.exam_store import FullExam


def readiness(full: FullExam) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    exam = full.exam
    spec = full.spec

    def add(check_id: str, status: str, label: str, detail: str | None = None) -> None:
        checks.append({"id": check_id, "status": status, "label": label, "detail": detail})

    if exam["status"] == "generating":
        add("generation", "blocked", "The exam is still being generated.")
    elif exam["status"] == "failed":
        add("generation", "serious", "Generation didn't finish.", exam.get("error_message"))
    elif exam["status"] == "building":
        add(
            "generation",
            "warning",
            "Build with AI isn't finished yet.",
            "Finish building to create the other versions and run the quality check.",
        )
    else:
        add("generation", "ok", "Generation complete.")

    primary = full.primary_version if full.versions else None
    primary_questions = full.version_questions(primary["id"]) if primary else []
    if not primary_questions:
        add("questions", "serious", "The exam has no questions.")

    missing_keys: list[str] = []
    for version in full.versions:
        for number, row in enumerate(full.version_questions(version["id"]), start=1):
            label = f"{version['label']}{number}" if len(full.versions) > 1 else f"Q{number}"
            choices = [choice.get("id") for choice in row.get("choices") or []]
            if row["type"] == "mcq":
                if row.get("correct_choice") not in choices:
                    missing_keys.append(label)
            elif not (row.get("answer") or "").strip() and not (row.get("solution") or "").strip():
                missing_keys.append(label)
    if missing_keys:
        add("answer_keys", "serious", "Some questions have no valid answer.", ", ".join(missing_keys[:20]))
    elif primary_questions:
        add("answer_keys", "ok", "Every question has an answer key.")

    flagged = [q for q in full.questions if q["needs_solution_review"]]
    if flagged:
        add(
            "solution_review",
            "warning",
            f"{len(flagged)} edited question(s) may need their answer key checked.",
            "Use “Regenerate solution” or review the key by hand.",
        )

    if len(full.versions) > 1 and primary:
        slots = {q["slot_id"] for q in primary_questions}
        uneven = [v["label"] for v in full.versions[1:] if {q["slot_id"] for q in full.version_questions(v["id"])} != slots]
        if uneven:
            add("versions", "serious", "Versions don't contain the same questions.", "Versions: " + ", ".join(uneven))
        else:
            add("versions", "ok", f"{len(full.versions)} equivalent versions.")

    review_status = exam.get("review_status")
    review = exam.get("review") or {}
    if review_status == "passed":
        add("review", "ok", "The AI quality check passed.")
    elif review_status == "needs_attention":
        open_issues = [
            i for i in review.get("issues", []) if i["severity"] in ("critical", "warning") and not i.get("fixApplied")
        ]
        critical = sum(1 for i in open_issues if i["severity"] == "critical")
        add(
            "review",
            "warning",
            f"The AI quality check found {len(open_issues)} issue(s) to look at"
            + (f", {critical} critical." if critical else "."),
        )
    elif review_status == "running":
        add("review", "warning", "The AI quality check is running.")
    else:
        add("review", "info", "The AI quality check hasn't been run since the last generation.")

    drafts = [q for q in primary_questions if q["status"] != "approved"]
    if primary_questions:
        if drafts:
            add("approval", "info", f"{len(primary_questions) - len(drafts)} of {len(primary_questions)} questions approved.")
        else:
            add("approval", "ok", "Every question is approved.")

    summaries = []
    for version in full.versions:
        summary = distribution.summarize(full.version_questions(version["id"]))
        warnings = distribution.compare(summary, spec)
        summaries.append({"label": version["label"], "summary": summary, "warnings": warnings})
        for warning in warnings:
            add(
                f"distribution_{version['label']}_{warning['dimension']}_{warning['label']}",
                "warning",
                (f"Version {version['label']}: " if len(full.versions) > 1 else "") + warning["message"],
            )

    blocked = any(check["status"] == "blocked" for check in checks)
    serious = [check for check in checks if check["status"] == "serious"]
    return {
        "canExport": not blocked and bool(primary_questions),
        "requiresConfirmation": bool(serious),
        "checks": checks,
        "versions": summaries,
        "durationMinutes": spec.duration_minutes if spec else None,
    }

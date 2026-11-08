"""The final review before export: what's complete, what needs attention, what blocks.

Three levels:

- blocked: export can't work yet (generation still running);
- serious: export only after the professor confirms (an incomplete generation,
  versions that don't match);
- warning / info: worth knowing, never blocking (unapproved questions, splits a
  little off target, the quality check not run).

Answer keys are written only when the professor asks, so questions without one (or with one written before
the question last changed) never hold back the questions: only downloading the answer key asks the
professor to confirm (see answerKeys, and the export endpoint).
"""

from typing import Any

from app.domain import distribution
from app.services.exam_store import FullExam, has_answer_key, integrity_leaks, leak_text, row_to_content


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

    keys = answer_key_status(full)
    if keys["missing"]:
        add(
            "answer_keys",
            "info",
            f"{len(keys['missing'])} of {keys['total']} questions have no answer key yet.",
            "Generate them when you're ready, before downloading the answer key: " + ", ".join(keys["missing"][:20]),
        )
    if keys["outdated"]:
        add(
            "answer_keys_outdated",
            "warning",
            f"{len(keys['outdated'])} answer key(s) were written before their question changed.",
            "Generate them again, or check them by hand: " + ", ".join(keys["outdated"][:20]),
        )
    if primary_questions and not keys["missing"] and not keys["outdated"]:
        add("answer_keys", "ok", "Every question has an up-to-date answer key.")

    # A question that gives away another's answer (checked in code, once the keys say what the answers are).
    for version in full.versions:
        rows = full.version_questions(version["id"])
        for helped, revealing, kind, text in integrity_leaks([row_to_content(row) for row in rows]):
            add(
                f"leak_{version['label']}_{revealing + 1}_{helped + 1}",
                "warning",
                (f"Version {version['label']}: " if len(full.versions) > 1 else "")
                + leak_text(kind, revealing + 1, helped + 1, text)
                + ".",
                f"Change question {revealing + 1} (e.g. name it, or use other values) or question {helped + 1}.",
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
        "answerKeys": keys,
        "versions": summaries,
        "durationMinutes": spec.duration_minutes if spec else None,
    }


def answer_key_status(full: FullExam, versions: list[str] | None = None) -> dict[str, Any]:
    """Which questions (in every version, or in these versions) have no valid answer key yet, and which
    keys are out of date."""
    missing: list[str] = []
    outdated: list[str] = []
    total = 0
    for version in full.versions:
        if versions is not None and version["label"] not in versions:
            continue
        for number, row in enumerate(full.version_questions(version["id"]), start=1):
            total += 1
            label = f"{version['label']}{number}" if len(full.versions) > 1 else f"Q{number}"
            choices = [choice.get("id") for choice in row.get("choices") or []]
            if not has_answer_key(row) or (row["type"] == "mcq" and row.get("correct_choice") not in choices):
                missing.append(label)
            elif row["needs_solution_review"]:
                outdated.append(label)
    return {"total": total, "missing": missing, "outdated": outdated}

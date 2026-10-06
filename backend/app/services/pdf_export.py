"""Printable PDFs of an exam: the student paper and the answer key, per version.

The student paper is built only from fields meant for students (question text,
choices, marks, figures). Answers, solutions, rubrics, explanations, sources
and any AI notes are never read when building it, so they can't leak into it.

Fonts: bundled Computer Modern for the reference exam's LaTeX typography,
with Symbol and an optional system Unicode font for additional characters.
"""

import io
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.rl_codecs import RL_Codecs
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    CondPageBreak,
    Flowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.exam_store import FullExam
from app.services.pdf_math import Equation, is_equation

RL_Codecs.register()

ASSETS = Path(__file__).resolve().parents[1] / "assets"
for name, filename in (
    ("ExamRoman", "cmr12.ttf"),
    ("ExamBold", "cmbx12.ttf"),
    ("ExamItalic", "cmti12.ttf"),
):
    pdfmetrics.registerFont(TTFont(name, str(ASSETS / "fonts" / filename)))
pdfmetrics.registerFontFamily("ExamRoman", normal="ExamRoman", bold="ExamBold", italic="ExamItalic", boldItalic="ExamBold")
_roman_chars = set(pdfmetrics.getFont("ExamRoman").face.charToGlyph)

_FALLBACK_FONT = "ProfPilotUnicode"
_FALLBACK_CANDIDATES = [
    os.environ.get("PDF_FALLBACK_FONT", ""),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "C:/Windows/Fonts/arial.ttf",
]
_fallback_registered: bool | None = None
_fallback_chars: set[int] = set()

INK = colors.black
MUTED = colors.HexColor("#5c5c5c")
RULE = colors.black
CODE_BACKGROUND = colors.HexColor("#f3f3f3")


def _fallback_font() -> bool:
    global _fallback_registered, _fallback_chars
    if _fallback_registered is None:
        _fallback_registered = False
        for path in _FALLBACK_CANDIDATES:
            if path and os.path.isfile(path):
                try:
                    font = TTFont(_FALLBACK_FONT, path)
                    pdfmetrics.registerFont(font)
                    _fallback_chars = set(font.face.charToGlyph)
                    _fallback_registered = True
                    break
                except Exception:
                    continue
    return _fallback_registered


def _encodable(character: str, codec: str) -> bool:
    try:
        character.encode(codec)
        return True
    except UnicodeEncodeError:
        return False


def _glyph(character: str) -> str | None:
    """Markup for one character in a font that has it, or None."""
    # BaKoMa uses TeX's legacy encoding: some Unicode aliases and ASCII slots
    # (e.g. superscript 2, underscore and vertical bar) map to unrelated glyphs.
    if ord(character) in _roman_chars and character.isascii() and (character.isalnum() or character in " .,;:!?+-=*/()[]'"):
        return escape(character)
    if _encodable(character, "winansi"):
        return f'<font name="Times-Roman">{escape(character)}</font>'
    if _encodable(character, "symbol"):
        return f'<font name="Symbol">{escape(character)}</font>'
    if _fallback_font() and ord(character) in _fallback_chars:
        return f'<font name="{_FALLBACK_FONT}">{escape(character)}</font>'
    return None


def markup(text: str, *, preserve_spaces: bool = False) -> str:
    """Escaped Paragraph markup, with characters routed to a font that has them."""
    out: list[str] = []
    for character in text:
        if character == "\n":
            out.append("<br/>")
            continue
        if character == " " and preserve_spaces:
            out.append("&nbsp;")
            continue
        glyph = _glyph(character)
        if glyph is None:
            # Subscripts and superscripts (xᵢ, x⁴) become real sub/superscript text;
            # other unusual characters fall back to their plain form (ﬁ -> fi).
            name = unicodedata.name(character, "")
            plain = unicodedata.normalize("NFKC", character)
            inner = "".join(_glyph(part) or "?" for part in plain) if plain != character else "?"
            if "SUBSCRIPT" in name:
                glyph = f"<sub>{inner}</sub>"
            elif "SUPERSCRIPT" in name:
                glyph = f"<super>{inner}</super>"
            else:
                glyph = inner
        out.append(glyph)
    return "".join(out)


# -----------------------------------------------------------------------------
# Styles
# -----------------------------------------------------------------------------

BODY = ParagraphStyle("body", fontName="ExamRoman", fontSize=12, leading=17, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=9.5, leading=12, textColor=MUTED)
HEADER = ParagraphStyle("header", parent=BODY, fontName="ExamBold", leading=18, alignment=TA_RIGHT)
META = ParagraphStyle("meta", parent=BODY, fontSize=10, leading=14, alignment=TA_RIGHT)
SECTION = ParagraphStyle(
    "section", parent=BODY, fontName="ExamBold", fontSize=12.5, leading=16, spaceBefore=10, keepWithNext=True
)
QUESTION = ParagraphStyle("question", parent=BODY, leftIndent=0.6 * cm)
QUESTION_LEAD = ParagraphStyle("question-lead", parent=QUESTION, firstLineIndent=-0.6 * cm, keepWithNext=True)
CHOICE = ParagraphStyle("choice", parent=BODY, leftIndent=0.6 * cm, firstLineIndent=0, spaceBefore=6)
SUBPART = ParagraphStyle("subpart", parent=BODY, leftIndent=0.6 * cm)
KEY_LABEL = ParagraphStyle("key", parent=BODY, fontName="ExamBold", spaceBefore=8, spaceAfter=3, keepWithNext=True)
CODE = ParagraphStyle("code", parent=BODY, fontName="Courier", fontSize=9, leading=11.5)


def inline_markup(text: str) -> str:
    """Readable legacy variable subscripts and exponent notation within prose."""
    pattern = r"(?<![A-Za-z])([xwyzpGh])([0-9]+)|\b([xwyzpGh])_([A-Za-z0-9]+)|\^\(([^()]+)\)|\^([−-]?[0-9]+)"
    result = []
    last = 0
    for match in re.finditer(pattern, text):
        result.append(markup(text[last : match.start()]))
        if match[1] or match[3]:
            result.append(markup(match[1] or match[3]) + "<sub>" + markup(match[2] or match[4]) + "</sub>")
        else:
            result.append("<super>" + markup(match[5] or match[6]) + "</super>")
        last = match.end()
    result.append(markup(text[last:]))
    return "".join(result)


def _table_at(lines: list[str], start: int) -> tuple[list[list[str]], int] | None:
    """Recognize explicit pipe tables and unambiguous legacy numeric tables."""
    pipe = "|" in lines[start]

    def cells(line: str) -> list[str]:
        if pipe:
            return [cell.strip() for cell in line.strip().strip("|").split("|")]
        # Older generated tables sometimes have single-space numeric columns.
        line = re.sub(
            r"(Standard deviation|Validation loss|Training error|Cross-validation error|Training examples|Desired p\(y = 1\))",
            lambda m: m[0].replace(" ", "\x00"),
            line,
        )
        return [cell.replace("\x00", " ") for cell in re.split(r"\s+", line.strip())]

    rows = [cells(lines[start])]
    count = len(rows[0])
    if not 2 <= count <= 10:
        return None
    end = start + 1
    while end < len(lines) and lines[end].strip():
        if pipe and "|" not in lines[end]:
            break
        row = cells(lines[end])
        if not pipe and count == 2 and len(row) != count:
            row = lines[end].strip().split(maxsplit=1)
        if len(row) != count:
            break
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in row):
            end += 1
            continue
        if not pipe and not all(re.fullmatch(r"[−+-]?\d+(?:\.\d+)?%?", cell) or is_equation(cell) for cell in row[1:]):
            break
        rows.append(row)
        end += 1
    return (rows, end) if len(rows) >= (2 if pipe else 3) else None


def _data_table(rows: list[list[str]], style: ParagraphStyle) -> Flowable:
    count = len(rows[0])
    cell_style = ParagraphStyle("table-cell", parent=BODY, fontSize=10 if count > 6 else 11, leading=14)
    weights = [
        min(160, max(32, max(pdfmetrics.stringWidth(row[i], "Times-Roman", cell_style.fontSize) for row in rows) + 16))
        for i in range(count)
    ]
    width = A4[0] - 5.08 * cm - style.leftIndent
    table = Table(
        [
            [Paragraph(("<b>" if r == 0 else "") + inline_markup(cell) + ("</b>" if r == 0 else ""), cell_style) for cell in row]
            for r, row in enumerate(rows)
        ],
        colWidths=[width * weight / sum(weights) for weight in weights],
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 0.7, INK),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, INK),
                ("LINEBELOW", (0, -1), (-1, -1), 0.7, INK),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    table.spaceBefore = 8
    table.spaceAfter = 10
    return table


def rich_text(text: str | None, style: ParagraphStyle = BODY) -> list[Flowable]:
    """Preserve paragraphs and render data tables, display equations and code."""
    flowables: list[Flowable] = []
    if not text:
        return flowables
    parts = re.split(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", text, flags=re.S)
    for index, part in enumerate(parts):
        if index % 2 == 1:  # code
            code = markup(part.rstrip("\n"), preserve_spaces=True)
            table = Table([[Paragraph(code, CODE)]], colWidths=["100%"])
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), CODE_BACKGROUND),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            flowables.append(table)
            continue
        lines = part.strip().splitlines()
        prose: list[str] = []

        def flush(prose: list[str] = prose) -> None:
            if prose:
                flowables.append(Paragraph(inline_markup("\n".join(prose)), style))
                prose.clear()

        line = 0
        while line < len(lines):
            content = lines[line].strip()
            if not content:
                flush()
                if flowables and not isinstance(flowables[-1], Spacer):
                    flowables.append(Spacer(1, 8))
                line += 1
                continue
            table = _table_at(lines, line)
            if table:
                flush()
                rows, line = table
                flowables.append(_data_table(rows, style))
                continue
            if is_equation(content):
                try:
                    equation = Equation(content)
                except (ValueError, RuntimeError):
                    equation = None  # Unsupported notation remains readable text.
                if equation is not None:
                    flush()
                    flowables.append(equation)
                    line += 1
                    continue
            prose.append(content)
            line += 1
        flush()
    return flowables


class _NumberedCanvas(pdf_canvas.Canvas):
    """Adds "Page X of Y" and a small footer label once the page count is known."""

    footer_left = ""
    footer_right = ""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved: list[dict[str, Any]] = []

    def showPage(self) -> None:  # noqa: N802 (ReportLab's name)
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self.setFont("ExamRoman", 9)
            self.setFillColor(MUTED)
            width = A4[0]
            self.drawCentredString(width / 2, 1.2 * cm, f"Page {self._pageNumber} of {total}")
            if self.footer_left:
                self.drawString(2.54 * cm, 1.2 * cm, self.footer_left[:45])
            if self.footer_right:
                self.drawRightString(width - 2.54 * cm, 1.2 * cm, self.footer_right)
            super().showPage()
        super().save()


@dataclass
class ExportHeader:
    course: str | None
    title: str
    duration_minutes: int | None


def _marks(points: float) -> str:
    return f"{points:g} mark" + ("" if points == 1 else "s")


class PdfExportService:
    def build(
        self,
        full: FullExam,
        header: ExportHeader,
        version_label: str,
        *,
        answer_key: bool,
        figures: dict[str, bytes],
    ) -> bytes:
        version = full.version_by_label(version_label)
        if version is None:
            raise ValueError("unknown version")
        questions = full.version_questions(version["id"])
        sections = {s["id"]: s for s in full.sections}
        multi = len(full.versions) > 1
        total = sum(float(q["points"]) for q in questions)

        title = header.title + (" - Answer key" if answer_key else "")
        heading = []
        if header.course and re.match(r"CMPS\b", header.course, flags=re.I):
            heading.extend(["Faculty of Arts and Sciences", "Department of Computer Science"])
        if header.course:
            heading.append(header.course)
        heading.append(title + (f" - {header.duration_minutes} min" if header.duration_minutes else ""))
        logo = Image(str(ASSETS / "aub-logo.png"), width=6.5 * cm, height=2.15 * cm, kind="proportional")
        width = A4[0] - 5.08 * cm
        banner = Table(
            [[logo, Paragraph("<br/>".join(markup(line) for line in heading), HEADER)]],
            colWidths=[6.7 * cm, width - 6.7 * cm],
        )
        banner.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story: list[Flowable] = [KeepTogether([_rule(), banner, _rule()])]
        meta = [
            f"Version {version_label}" if multi else None,
            f"Total: {_marks(total)}",
        ]
        story.append(Spacer(1, 6))
        story.append(Paragraph(markup(" | ".join(part for part in meta if part)), META))
        story.append(Spacer(1, 12))

        if not answer_key:
            story.append(Spacer(1, 6))
            line = "_" * 28
            info = Table(
                [[Paragraph(markup(f"Name: {line}"), BODY), Paragraph(markup(f"Student ID: {'_' * 17}"), BODY)]],
                colWidths=["58%", "42%"],
            )
            info.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
            story.append(info)
            story.append(_rule())
            story.append(Spacer(1, 6))
            count = len(questions)
            section_count = len({q.get("section_id") for q in questions if q.get("section_id")})
            facts = f"This exam has {count} question{'s' if count != 1 else ''}"
            if section_count > 1:
                facts += f" in {section_count} sections"
            facts += f", worth {_marks(total)} in total."
            instructions = [facts]
            if header.duration_minutes:
                instructions.append(f"You have {header.duration_minutes} minutes.")
            if any(q["type"] == "mcq" for q in questions):
                instructions.append("For multiple-choice questions, choose one answer.")
            story.append(Paragraph("<b>" + markup(" ".join(instructions)) + "</b>", BODY))
            story.append(Spacer(1, 12))
        else:
            story.append(Spacer(1, 4))
            story.append(Paragraph("Confidential: for markers only.", SMALL))
            story.append(Spacer(1, 6))

        current_section = None
        for number, question in enumerate(questions, start=1):
            section_id = question.get("section_id")
            if section_id and section_id != current_section and section_id in sections:
                current_section = section_id
                section = sections[section_id]
                section_points = sum(float(q["points"]) for q in questions if q.get("section_id") == section_id)
                story.append(Paragraph(markup(f"{section['title']} ({_marks(section_points)})"), SECTION))
                story.append(_rule())
                if section.get("instructions"):
                    story.extend(rich_text(section["instructions"], SMALL))
                story.append(Spacer(1, 4))
            block = self._question(question, number, answer_key=answer_key, figures=figures)
            # Leave room for a useful part of the stem, not just its opening line.
            story.append(CondPageBreak(100))
            # A table or equation can be tall despite having few flowables.
            # Let stems paginate naturally; the MCQ choices stay together.
            story.extend(block)
            story.append(Spacer(1, 24))

        buffer = io.BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=2.54 * cm,
            rightMargin=2.54 * cm,
            topMargin=2.1 * cm,
            bottomMargin=2 * cm,
            title=title,
            author="",
            subject="",
            creator="",
            producer="",
        )

        class Canvas(_NumberedCanvas):
            footer_left = header.title
            footer_right = f"Version {version_label}" if multi else ""

        document.build(story, canvasmaker=Canvas)
        return buffer.getvalue()

    def _question(self, q: dict[str, Any], number: int, *, answer_key: bool, figures: dict[str, bytes]) -> list[Flowable]:
        block: list[Flowable] = []
        prompt = rich_text(q["prompt"], QUESTION)
        lead = f"{number})  "
        points = f"  <i>[{_marks(float(q['points']))}]</i>"
        if prompt and isinstance(prompt[0], Paragraph):
            first = prompt[0]
            prompt[0] = Paragraph(lead + first.text + points, QUESTION_LEAD)
        else:
            block.append(Paragraph(lead + points, QUESTION_LEAD))
        block.extend(prompt)

        figure = q.get("figure_document_id")
        if figure and figure in figures:
            image = _image(figures[figure])
            if image is not None:
                block.extend([Spacer(1, 6), image, Spacer(1, 4)])

        choice_block: list[Flowable] = []
        for choice in q.get("choices") or []:
            correct = answer_key and choice.get("id") == q.get("correct_choice")
            text = f"{escape(str(choice.get('id', '')).lower())})  {inline_markup(str(choice.get('text', '')))}"
            if correct:
                text = f'<b>{text}</b>  <font name="ZapfDingbats">\u2714</font>'
            choice_block.append(Paragraph(text, CHOICE))
        if choice_block:
            block.append(KeepTogether(choice_block))

        for part in q.get("subparts") or []:
            block.append(Spacer(1, 3))
            block.append(
                Paragraph(
                    f"<b>({escape(str(part.get('label', '')))})</b>  {markup(str(part.get('prompt', '')))}  "
                    f"<i>[{_marks(float(part.get('points', 0)))}]</i>",
                    SUBPART,
                )
            )
            if answer_key:
                if part.get("answer"):
                    block.append(Paragraph("<b>Answer:</b> " + markup(str(part["answer"])), SUBPART))
                if part.get("rubric"):
                    block.append(_rubric(part["rubric"], indent=True))

        if answer_key:
            block.append(Spacer(1, 4))
            if q["type"] == "mcq":
                block.append(Paragraph(f"Correct answer: {escape(str(q.get('correct_choice') or '—'))}", KEY_LABEL))
            if q.get("answer") and not q.get("subparts"):
                block.append(Paragraph("Answer", KEY_LABEL))
                block.extend(rich_text(q["answer"]))
            if q.get("solution"):
                block.append(Paragraph("Solution", KEY_LABEL))
                block.extend(rich_text(q["solution"]))
            if q.get("explanation"):
                block.append(Paragraph("Why the other choices are wrong" if q["type"] == "mcq" else "Notes", KEY_LABEL))
                block.extend(rich_text(q["explanation"]))
            if q.get("rubric"):
                block.append(Paragraph("Marking rubric", KEY_LABEL))
                block.append(_rubric(q["rubric"]))
        return block

    def build_many(
        self,
        full: FullExam,
        header: ExportHeader,
        *,
        versions: list[str],
        kinds: list[str],
        figures: dict[str, bytes],
    ) -> tuple[bytes, str, str]:
        """One PDF, or a ZIP of several. Returns (content, media type, file name)."""
        base = _slug(header.title)
        files: list[tuple[str, bytes]] = []
        multi = len(full.versions) > 1
        for label in versions:
            for kind in kinds:
                content = self.build(full, header, label, answer_key=kind == "answer_key", figures=figures)
                suffix = "answer-key" if kind == "answer_key" else "exam"
                name = f"{base}{f'-version-{label}' if multi else ''}-{suffix}.pdf"
                files.append((name, content))
        if len(files) == 1:
            return files[0][1], "application/pdf", files[0][0]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in files:
                archive.writestr(name, content)
        return buffer.getvalue(), "application/zip", f"{base}-exports.zip"


def _rule() -> Flowable:
    table = Table([[""]], colWidths=["100%"], rowHeights=[1])
    table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.6, RULE)]))
    return table


def _rubric(items: list[dict[str, Any]], *, indent: bool = False) -> Flowable:
    rows = [[Paragraph("<b>Criterion</b>", SMALL), Paragraph("<b>Marks</b>", SMALL)]]
    for item in items:
        rows.append(
            [Paragraph(markup(str(item.get("criterion", ""))), BODY), Paragraph(f"{float(item.get('points', 0)):g}", BODY)]
        )
    rows.append([Paragraph("<b>Total</b>", BODY), Paragraph(f"<b>{sum(float(i.get('points', 0)) for i in items):g}</b>", BODY)])
    table = Table(rows, colWidths=["82%", "18%"], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, RULE),
                ("BACKGROUND", (0, 0), (-1, 0), CODE_BACKGROUND),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    if indent:
        wrapper = Table([[table]], colWidths=["100%"])
        wrapper.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0.6 * cm)]))
        return wrapper
    return table


def _image(data: bytes) -> Flowable | None:
    try:
        reader = ImageReader(io.BytesIO(data))
        width, height = reader.getSize()
    except Exception:
        return None
    max_width, max_height = 13 * cm, 9 * cm
    scale = min(max_width / width, max_height / height, 1.0)
    image = Image(io.BytesIO(data), width=width * scale, height=height * scale)
    image.hAlign = "CENTER"
    return image


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:60] or "exam"

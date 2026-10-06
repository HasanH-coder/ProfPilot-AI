"""Printable PDFs of an exam: the student paper and the answer key, per version.

The student paper is built only from fields meant for students (question text,
choices, marks, figures). Answers, solutions, rubrics, explanations, sources
and any AI notes are never read when building it, so they can't leak into it.

Fonts: the standard PDF Times family, with the built-in Symbol font for Greek
letters and mathematical symbols, and an optional system Unicode font for
anything else. No font files are bundled or downloaded.
"""

import io
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.rl_codecs import RL_Codecs
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
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

RL_Codecs.register()

_FALLBACK_FONT = "ProfPilotUnicode"
_FALLBACK_CANDIDATES = [
    os.environ.get("PDF_FALLBACK_FONT", ""),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]
_fallback_registered: bool | None = None
_fallback_chars: set[int] = set()

INK = colors.HexColor("#171717")
MUTED = colors.HexColor("#5c5c5c")
RULE = colors.HexColor("#c8c8c8")
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
    if _encodable(character, "winansi"):
        return escape(character)
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

BODY = ParagraphStyle("body", fontName="Times-Roman", fontSize=11, leading=14.5, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=9.5, leading=12, textColor=MUTED)
TITLE = ParagraphStyle("title", parent=BODY, fontName="Times-Bold", fontSize=17, leading=21, alignment=TA_CENTER)
COURSE = ParagraphStyle("course", parent=BODY, fontSize=11.5, leading=15, alignment=TA_CENTER)
META = ParagraphStyle("meta", parent=BODY, fontSize=10.5, leading=14, alignment=TA_CENTER, textColor=MUTED)
SECTION = ParagraphStyle("section", parent=BODY, fontName="Times-Bold", fontSize=12.5, leading=16, spaceBefore=10)
CHOICE = ParagraphStyle("choice", parent=BODY, leftIndent=0.6 * cm, firstLineIndent=0)
SUBPART = ParagraphStyle("subpart", parent=BODY, leftIndent=0.6 * cm)
KEY_LABEL = ParagraphStyle("key", parent=BODY, fontName="Times-Bold", spaceBefore=4)
CODE = ParagraphStyle("code", parent=BODY, fontName="Courier", fontSize=9, leading=11.5)


def rich_text(text: str | None, style: ParagraphStyle = BODY) -> list[Flowable]:
    """Paragraphs for plain text with line breaks and ``` code blocks."""
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
        for paragraph in re.split(r"\n{2,}", part.strip()):
            if paragraph.strip():
                flowables.append(Paragraph(markup(paragraph.strip()), style))
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
            self.setFont("Times-Roman", 9)
            self.setFillColor(MUTED)
            width = A4[0]
            self.drawCentredString(width / 2, 1.2 * cm, f"Page {self._pageNumber} of {total}")
            if self.footer_left:
                self.drawString(2 * cm, 1.2 * cm, self.footer_left[:70])
            if self.footer_right:
                self.drawRightString(width - 2 * cm, 1.2 * cm, self.footer_right)
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

        story: list[Flowable] = []
        if header.course:
            story.append(Paragraph(markup(header.course), COURSE))
        title = header.title + (" — Answer key" if answer_key else "")
        story.append(Paragraph(markup(title), TITLE))
        meta = [
            f"Version {version_label}" if multi else None,
            f"Duration: {header.duration_minutes} minutes" if header.duration_minutes else None,
            f"Total: {_marks(total)}",
        ]
        story.append(Spacer(1, 4))
        story.append(Paragraph(markup("   ·   ".join(part for part in meta if part)), META))
        story.append(Spacer(1, 10))
        story.append(_rule())

        if not answer_key:
            story.append(Spacer(1, 6))
            line = "_" * 34
            info = Table(
                [[Paragraph(f"Name: {line}", BODY), Paragraph(f"Student ID: {'_' * 22}", BODY)]],
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
            story.append(Paragraph("<b>Instructions</b>", BODY))
            for item in instructions:
                story.append(Paragraph(markup(f"•  {item}"), BODY))
            story.append(Spacer(1, 8))
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
            # Keep a short question on one page; let a long one flow across pages.
            if len(block) < 14:
                story.append(KeepTogether(block))
            else:
                story.extend(block)
            story.append(Spacer(1, 10))

        buffer = io.BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=2 * cm,
            rightMargin=2 * cm,
            topMargin=1.8 * cm,
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
        prompt = rich_text(q["prompt"])
        lead = f"<b>{number}.</b>  <i>({_marks(float(q['points']))})</i>  "
        if prompt and isinstance(prompt[0], Paragraph):
            first = prompt[0]
            prompt[0] = Paragraph(lead + first.text, BODY)
        else:
            block.append(Paragraph(lead, BODY))
        block.extend(prompt)

        figure = q.get("figure_document_id")
        if figure and figure in figures:
            image = _image(figures[figure])
            if image is not None:
                block.extend([Spacer(1, 6), image, Spacer(1, 4)])

        for choice in q.get("choices") or []:
            correct = answer_key and choice.get("id") == q.get("correct_choice")
            text = f"{choice.get('id')}.  {markup(str(choice.get('text', '')))}"
            if correct:
                text = f'<b>{text}</b>  <font name="ZapfDingbats">\u2714</font>'
            block.append(Paragraph(text, CHOICE))

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

"""Typeset conservative, standalone Unicode equations as selectable PDF glyphs."""

import hashlib
import re
import unicodedata
from functools import lru_cache
from threading import Lock

from matplotlib.font_manager import FontProperties
from matplotlib.mathtext import MathTextParser
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable

_lock = Lock()
_parser = MathTextParser("path")
_property = FontProperties(size=12, math_fontfamily="cm")
_symbols = {
    "α": r"\alpha ",
    "β": r"\beta ",
    "θ": r"\theta ",
    "λ": r"\lambda ",
    "φ": r"\phi ",
    "π": r"\pi ",
    "τ": r"\tau ",
    "Σ": r"\sum ",
    "∇": r"\nabla ",
    "≤": r"\leq ",
    "≥": r"\geq ",
    "≈": r"\approx ",
    "→": r"\to ",
    "←": r"\leftarrow ",
    "−": "-",
    "·": r"\cdot ",
    "′": "'",
}


def is_equation(line: str) -> bool:
    """Do not reinterpret prose, definitions with sentences, or data rows as math."""
    if len(line) > 300 or not re.search(r"=|≈|←", line):
        return False
    allowed = {"Recall", "Precision", "Accuracy", "exp", "ln", "log", "min", "max"}
    return all(len(word) <= 3 or word in allowed for word in re.findall(r"[A-Za-z]+", line))


def _atom_end(text: str, start: int) -> int:
    """End of a signed atom, function call, or power, without evaluating it."""
    position = start
    while position < len(text) and text[position].isspace():
        position += 1
    if position < len(text) and text[position] in "+-−":
        position += 1
    while position < len(text) and (text[position].isalnum() or text[position] in "_."):
        position += 1
    if position < len(text) and text[position] == "(":
        depth = 1
        position += 1
        while position < len(text) and depth:
            if text[position] == "(":
                depth += 1
            elif text[position] == ")":
                depth -= 1
            position += 1
        if depth:
            return start
    if position < len(text) and text[position] == "^":
        position = _atom_end(text, position + 1)
    return position


def math_source(text: str) -> str:
    """Preserve the expression; change notation only, without evaluating it."""
    text = text.strip().rstrip(".;")
    # Work on original notation, so TeX command names cannot become operands.
    slash = text.find("/")
    if slash >= 1 and slash < len(text) - 1:
        end = len(text[:slash].rstrip())
        candidates = [i for i in range(end) if (text[i].isalnum() or text[i] == "(") and _atom_end(text, i) == end]
        if not candidates:
            raise ValueError("Ambiguous fraction notation")
        left = min(candidates)
        right = _atom_end(text, slash + 1) - 1
        numerator = text[left:slash].strip()
        denominator = text[slash + 1 : right + 1].strip()
        if numerator and denominator:

            def unwrap(value: str) -> str:
                return value[1:-1] if value.startswith("(") and value.endswith(")") else value

            return (
                math_source(text[:left])
                + r"\frac{"
                + math_source(unwrap(numerator))
                + "}{"
                + math_source(unwrap(denominator))
                + "}"
                + math_source(text[right + 1 :])
            )
    text = re.sub(r"\^\(([^()]*)\)", r"^{\1}", text)
    text = re.sub(r"(?<![A-Za-z])([xwyzb])([0-9]+)", r"\1_{\2}", text)
    text = re.sub(r"_([A-Za-z0-9]+)", r"_{\1}", text)
    text = re.sub(r"([₀-₉ᵢⱼ]+)", lambda m: "_{" + unicodedata.normalize("NFKC", m[0]) + "}", text)
    text = re.sub(r"([⁰¹²³⁴⁵⁶⁷⁸⁹]+)", lambda m: "^{" + unicodedata.normalize("NFKC", m[0]) + "}", text)
    text = re.sub(r"\b(Recall|Precision|Accuracy|TP|TN|FP|FN)\b", lambda m: r"\mathrm{" + m[0] + "}", text)
    text = re.sub(r"\b(exp|ln|log)\b", lambda m: "\\" + m[0] + " ", text)
    for character, replacement in _symbols.items():
        text = text.replace(character, replacement)
    return text


@lru_cache(maxsize=128)
def _layout(text: str):
    with _lock:
        return _parser.parse("$" + math_source(text) + "$", dpi=72, prop=_property)


class Equation(Flowable):
    """A centered vector equation, scaled to the available column width."""

    def __init__(self, text: str):
        super().__init__()
        self.layout = _layout(text)
        if self.layout.width > 650:
            raise ValueError("Use wrapped text rather than an unreadably small equation")
        self.spaceBefore = 5
        self.spaceAfter = 6
        self.scale = 1.0
        self.fonts = {}
        with _lock:
            for font, _, _, _, _, _ in self.layout.glyphs:
                path = str(font.fname)
                name = "Math" + hashlib.sha256(path.encode()).hexdigest()[:12]
                if name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(name, path))
                self.fonts[path] = name

    def wrap(self, availWidth, availHeight):  # noqa: N803
        self.scale = min(1.0, availWidth / max(self.layout.width, 1))
        self.width = availWidth
        self.height = self.layout.height * self.scale + 8
        return self.width, self.height

    def draw(self):
        self.canv.saveState()
        self.canv.translate((self.width - self.layout.width * self.scale) / 2, 4 + self.layout.depth * self.scale)
        self.canv.scale(self.scale, self.scale)
        for font, size, codepoint, _, x, y in self.layout.glyphs:
            self.canv.setFont(self.fonts[str(font.fname)], size)
            self.canv.drawString(x, y, chr(codepoint))
        for x, y, width, height in self.layout.rects:
            self.canv.rect(x, y, width, height, stroke=0, fill=1)
        self.canv.restoreState()

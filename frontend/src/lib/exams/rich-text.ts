/** Render existing Unicode/plain-text questions as well as newly generated LaTeX. */
export type ExamTextBlock =
  | { kind: "paragraph" | "code"; text: string }
  | { kind: "math"; source: string; original: string }
  | { kind: "table"; rows: string[][] };

export function isEquation(line: string): boolean {
  const allowed = new Set(["Recall", "Precision", "Accuracy", "exp", "ln", "log", "min", "max"]);
  return line.length <= 300 && /[=≈←]/.test(line)
    && (line.match(/[A-Za-z]+/g) ?? []).every(word => word.length <= 3 || allowed.has(word));
}

function atomEnd(text: string, start: number): number {
  let position = start;
  while (/\s/.test(text[position] ?? "") && position < text.length) position++;
  if (/[+−-]/.test(text[position] ?? "")) position++;
  while (position < text.length && /[\p{L}\p{N}_.]/u.test(text[position])) position++;
  if (text[position] === "(") {
    let depth = 1;
    position++;
    while (position < text.length && depth) {
      if (text[position] === "(") depth++;
      if (text[position] === ")") depth--;
      position++;
    }
    if (depth) return start;
  }
  if (text[position] === "^") position = atomEnd(text, position + 1);
  return position;
}

const symbols: Record<string, string> = {
  α: "\\alpha ", β: "\\beta ", θ: "\\theta ", λ: "\\lambda ", φ: "\\phi ", π: "\\pi ", τ: "\\tau ",
  Σ: "\\sum ", "∇": "\\nabla ", "≤": "\\leq ", "≥": "\\geq ", "≈": "\\approx ",
  "→": "\\to ", "←": "\\leftarrow ", "−": "-", "·": "\\cdot ", "′": "'",
};

export function legacyMath(text: string): string {
  text = text.trim().replace(/[.;]+$/, "");
  const slash = text.indexOf("/");
  if (slash > 0 && slash < text.length - 1) {
    const end = text.slice(0, slash).trimEnd().length;
    const candidates = Array.from({ length: end }, (_, i) => i)
      .filter(i => /[\p{L}\p{N}(]/u.test(text[i]) && atomEnd(text, i) === end);
    if (!candidates.length) throw new Error("Ambiguous fraction notation");
    const left = candidates[0];
    const right = atomEnd(text, slash + 1);
    const unwrap = (value: string) => value.startsWith("(") && value.endsWith(")") ? value.slice(1, -1) : value;
    return legacyMath(text.slice(0, left)) + "\\frac{" + legacyMath(unwrap(text.slice(left, slash).trim()))
      + "}{" + legacyMath(unwrap(text.slice(slash + 1, right).trim())) + "}" + legacyMath(text.slice(right));
  }
  text = text.replace(/\^\(([^()]*)\)/g, "^{$1}")
    .replace(/(?<![A-Za-z])([xwyzb])([0-9]+)/g, "$1_{$2}")
    .replace(/_([A-Za-z0-9]+)/g, "_{$1}")
    .replace(/[₀-₉ᵢⱼ]+/g, value => "_{" + value.normalize("NFKC") + "}")
    .replace(/[⁰¹²³⁴⁵⁶⁷⁸⁹]+/g, value => "^{" + value.normalize("NFKC") + "}")
    .replace(/\b(Recall|Precision|Accuracy|TP|TN|FP|FN)\b/g, "\\mathrm{$1}")
    .replace(/\b(exp|ln|log)\b/g, "\\$1 ");
  return Array.from(text, character => symbols[character] ?? character).join("");
}

function tableAt(lines: string[], start: number): { rows: string[][]; end: number } | null {
  const pipe = lines[start].includes("|");
  const cells = (line: string) => pipe
    ? line.trim().replace(/^\||\|$/g, "").split("|").map(cell => cell.trim())
    : line.replace(/Standard deviation|Validation loss|Training error|Cross-validation error|Training examples|Desired p\(y = 1\)/g,
      label => label.replaceAll(" ", "\0")).trim().split(/\s+/).map(cell => cell.replaceAll("\0", " "));
  const rows = [cells(lines[start])];
  const count = rows[0].length;
  if (count < 2 || count > 10) return null;
  let end = start + 1;
  while (end < lines.length && lines[end].trim()) {
    if (pipe && !lines[end].includes("|")) break;
    let row = cells(lines[end]);
    if (!pipe && count === 2 && row.length !== count) {
      const match = lines[end].trim().match(/^(\S+)\s+(.+)$/);
      if (match) row = [match[1], match[2]];
    }
    if (row.length !== count) break;
    if (row.every(cell => /^:?-{3,}:?$/.test(cell))) { end++; continue; }
    if (!pipe && !row.slice(1).every(cell => /^[−+-]?\d+(?:\.\d+)?%?$/.test(cell) || isEquation(cell))) break;
    rows.push(row);
    end++;
  }
  return rows.length >= (pipe ? 2 : 3) ? { rows, end } : null;
}

export function parseExamText(text: string): ExamTextBlock[] {
  const blocks: ExamTextBlock[] = [];
  for (const chunk of text.split(/(```[\s\S]*?```|\\\[[\s\S]*?\\\]|\$\$[\s\S]*?\$\$)/g)) {
    if (chunk.startsWith("```")) {
      blocks.push({ kind: "code", text: chunk.replace(/^```[\w+-]*\n?/, "").replace(/```$/, "").replace(/\n$/, "") });
      continue;
    }
    if (chunk.startsWith("\\[") || chunk.startsWith("$$")) {
      blocks.push({ kind: "math", source: chunk.slice(2, -2).trim(), original: chunk });
      continue;
    }
    const lines = chunk.trim().split("\n");
    let prose: string[] = [];
    const flush = () => { if (prose.length) blocks.push({ kind: "paragraph", text: prose.join("\n") }); prose = []; };
    for (let line = 0; line < lines.length;) {
      const content = lines[line].trim();
      if (!content) { flush(); line++; continue; }
      const table = tableAt(lines, line);
      if (table) { flush(); blocks.push({ kind: "table", rows: table.rows }); line = table.end; continue; }
      if (isEquation(content)) {
        try { const source = legacyMath(content); flush(); blocks.push({ kind: "math", source, original: content }); line++; continue; }
        catch { /* Preserve unsupported notation verbatim. */ }
      }
      prose.push(content);
      line++;
    }
    flush();
  }
  return blocks;
}

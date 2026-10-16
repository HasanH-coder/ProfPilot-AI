import katex from "katex";
import type { ReactNode } from "react";
import "katex/dist/katex.min.css";

import { parseExamText } from "@/lib/exams/rich-text";
import { cn } from "@/lib/utils";

function MathFormula({ source, original, display = false }: { source: string; original: string; display?: boolean }) {
  let html: string | null = null;
  try {
    if (source.length > 1000) throw new Error("Equation too long");
    html = katex.renderToString(source, { displayMode: display, throwOnError: true, trust: false, maxExpand: 200, maxSize: 10, output: "htmlAndMathml" });
  } catch {
    // Keep unsupported notation visible for review.
  }
  return html === null ? <span className="whitespace-pre-wrap">{original}</span>
    : <span className={display ? "exam-display-math" : "exam-inline-math"} dangerouslySetInnerHTML={{ __html: html }} />;
}

export function InlineQuestionText({ text }: { text: string }) {
  const tokens = text.split(/(\\\([\s\S]*?\\\)|(?<!\\)\$(?!\$)[^$\n]+?(?<!\\)\$(?!\$))/g);
  const output: ReactNode[] = [];
  tokens.forEach((token, index) => {
    if (index % 2) {
      output.push(<MathFormula key={index} source={token.startsWith("\\(") ? token.slice(2, -2) : token.slice(1, -1)} original={token} />);
    } else {
      const parts = token.split(/((?<![A-Za-z])[xwyzpGh][0-9]+|\b[xwyzpGh]_[A-Za-z0-9]+)/g);
      parts.forEach((part, subindex) => {
        output.push(subindex % 2
          ? <MathFormula key={`${index}-${subindex}`} source={part.replace(/^([xwyzpGh])_?(.+)$/, "$1_{$2}")} original={part} />
          : part);
      });
    }
  });
  return <>{output}</>;
}

/**
 * Structured question content: prose, tables, LaTeX and literal code.
 */
export function QuestionText({ text, className }: { text: string | null | undefined; className?: string }) {
  if (!text) return null;
  const blocks = parseExamText(text);
  return (
    <div className={cn("exam-rich-text flex min-w-0 flex-col gap-4 text-sm leading-relaxed", className)}>
      {blocks.map((block, index) =>
        block.kind === "code" ? (
          <pre key={index} className="overflow-x-auto rounded-lg bg-muted px-3 py-2 font-mono text-[0.8rem] leading-snug">
            <code>{block.text}</code>
          </pre>
        ) : block.kind === "math" ? (
          <div key={index} className="overflow-x-auto py-2"><MathFormula source={block.source} original={block.original} display /></div>
        ) : block.kind === "table" ? (
          <div key={index} className="overflow-x-auto rounded-lg border">
            <table className="w-full border-collapse text-left text-sm">
              <thead className="bg-muted/60"><tr>{block.rows[0].map((cell, i) => <th key={i} scope="col" className="border-b px-4 py-3 font-medium"><InlineQuestionText text={cell} /></th>)}</tr></thead>
              <tbody>{block.rows.slice(1).map((row, r) => <tr key={r} className="border-b last:border-0 even:bg-muted/20">{row.map((cell, c) => <td key={c} className="px-4 py-3 align-middle"><InlineQuestionText text={cell} /></td>)}</tr>)}</tbody>
            </table>
          </div>
        ) : <p key={index} className="break-words whitespace-pre-wrap"><InlineQuestionText text={block.text} /></p>,
      )}
    </div>
  );
}

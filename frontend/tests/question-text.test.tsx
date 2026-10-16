import { expect, test } from "vitest";
import { render } from "vitest-browser-react";

import { QuestionText } from "@/components/exam/question-text";
import { legacyMath, parseExamText } from "@/lib/exams/rich-text";

test("renders the existing exam's pipe tables and fractions as structured content", async () => {
  await render(<QuestionText text={"Probability p | Actual disease present | Actual disease absent\n0.82 | 3 | 0\n0.56 | 1 | 5\n0.34 | 1 | 6\n0.12 | 0 | 4\n\nRecall = TP/(TP + FN)"} />);
  expect(document.querySelectorAll("table tbody tr")).toHaveLength(4);
  expect(document.querySelectorAll('th[scope="col"]')).toHaveLength(3);
  expect(document.querySelector("table")?.textContent).not.toContain("|");
  expect(document.querySelector("math mfrac")).not.toBeNull();
});

test("supports inline and display LaTeX, including math in table cells", async () => {
  await render(<QuestionText text={String.raw`Use \(x_1^2\) to calculate the result.

\[J = \frac{1}{4}\sum_{i=1}^{4} -y_i\ln p_i\]

| Feature | Value |
| --- | --- |
| \(x_1\) | \(\frac{1}{2}\) |`} />);
  expect(document.querySelectorAll(".katex")).toHaveLength(4);
  expect(document.querySelector(".exam-display-math math mfrac")).not.toBeNull();
  expect(document.querySelector("td math msub")).not.toBeNull();
});

test("preserves prose, code and unsupported formulas without executing HTML", async () => {
  const literal = '<img src=x onerror="alert(1)">';
  await render(<QuestionText text={`${literal}\nA model outputs P(y = 1 | x).\n\n\`\`\`python\nprint('$x$')\n\`\`\`\n\n\\[\\unsupported{x}\\]`} />);
  expect(document.querySelector(".exam-rich-text img")).toBeNull();
  expect(document.querySelector("table")).toBeNull();
  expect(document.querySelector("code")?.textContent).toBe("print('$x$')");
  expect(document.querySelector(".exam-rich-text")?.textContent).toContain(String.raw`\unsupported{x}`);
});

test("legacy notation keeps powers, grouping and negative signs", () => {
  expect(legacyMath("J = x^2/y")).toBe(String.raw`J =\frac{x^2}{y}`);
  expect(legacyMath("J = a-x/y")).toBe(String.raw`J = a-\frac{x}{y}`);
  expect(legacyMath("J = 1/(1 + exp(-s))")).toBe(String.raw`J =\frac{1}{1 + \exp (-s)}`);
  expect(parseExamText("Feature Minimum Maximum Mean Standard deviation\nx1 0 2000 1000 250\nx2 0 2 1 0.25")[0].kind).toBe("table");
});

from pathlib import Path
import pypdfium2 as pdfium
from app.services.exam_store import FullExam
from app.services.pdf_export import ExportHeader, PdfExportService

root = Path(__file__).resolve().parents[2]
questions = [
    dict(id="q1", version_id="v", position=0, type="problem", points=5,
         prompt="\n\n".join("Explain the observation and justify your answer." for _ in range(15))),
    dict(id="q2", version_id="v", position=1, type="mcq", points=5,
         prompt=("A logistic classifier assigns the following disease probabilities.\n\n"
                 "Probability p | Actual disease present | Actual disease absent\n"
                 "0.82 | 3 | 0\n0.56 | 1 | 5\n0.34 | 1 | 6\n0.12 | 0 | 4\n\n"
                 r"Predict disease present when \(p \geq t\). Maximize precision subject to recall of at least 80%."
                 "\n\n" + r"\[\mathrm{Recall} = \frac{TP}{TP + FN}\]"
                 "\n\n" + r"\[\mathrm{Precision} = \frac{TP}{TP + FP}\]"
                 "\n\n" + r"\[J = \frac{1}{4}\sum_{i=1}^{4} -y_i\ln p_i\]"),
         choices=[dict(id="A", text=r"Select \(t = 0.50\)."), dict(id="B", text=r"Select \(t = 0.70\).")]),
]
full = FullExam(exam={}, versions=[dict(id="v", label="A")], sections=[], questions=questions)
path = root / "output/pdf/exam-latex-pagination-preview.pdf"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(PdfExportService().build(full, ExportHeader("CMPS 261 - Machine Learning", "Layout preview", 60), "A", answer_key=False, figures={}))
doc = pdfium.PdfDocument(str(path))
for i in range(len(doc)):
    page = doc[i]
    bitmap = page.render(scale=1.5)
    bitmap.to_pil().copy().save(root / f"tmp/pdfs/page-{i+1}.png")
print(f"Rendered {len(doc)} pages: {path}")

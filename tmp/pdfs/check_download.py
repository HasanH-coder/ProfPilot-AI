import io
import zipfile
from pathlib import Path
from pypdf import PdfReader
import pypdfium2 as pdfium
from PIL import Image

out = Path(__file__).resolve().parent
with zipfile.ZipFile(r"C:\Users\loq\Downloads\midterm-exports (4).zip") as archive:
    for name in archive.namelist():
        data = archive.read(name)
        texts = [page.extract_text() for page in PdfReader(io.BytesIO(data)).pages]
        found = next(i for i, text in enumerate(texts) if "2) A logistic classifier" in text)
        assert "Probability p" in texts[found] and "always-negative baseline" in texts[found]
        if "version-A" in name:
            assert all(value in texts[found] for value in ["0.82", "0.56", "0.34", "0.12"])
        print(name, len(texts), "pages; question 2 intact on page", found + 1)
        doc = pdfium.PdfDocument(data)
        sheet = Image.new("RGB", (4 * 300, ((len(doc) + 3) // 4) * 425), "#ddd")
        for i in range(len(doc)):
            page = doc[i]
            bitmap = page.render(scale=0.5)
            image = bitmap.to_pil().copy().convert("RGB")
            image.thumbnail((300, 425))
            sheet.paste(image, ((i % 4) * 300, (i // 4) * 425))
            if i == found:
                bitmap = page.render(scale=1.5)
                bitmap.to_pil().copy().save(out / (name + ".png"))
        sheet.save(out / (name + "-all.png"))

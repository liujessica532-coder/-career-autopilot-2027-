import hashlib
import shutil
from pathlib import Path
from pypdf import PdfReader
from PIL import Image
from app.db import Candidate, HumanAction

def import_candidate(session, pdf: Path, photo: Path | None = None):
    text = '\n'.join(page.extract_text() or '' for page in PdfReader(pdf).pages)
    if not text.strip():
        raise ValueError('PDF has no extractable text; human transcription/OCR review required')
    root = Path('artifacts/candidates') / hashlib.sha256(pdf.read_bytes()).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    target = root / 'source.pdf'
    if pdf.resolve() != target.resolve():
        shutil.copyfile(pdf, target)
    photo_path = None
    if photo:
        with Image.open(photo) as image:
            image.verify()
        photo_path = root / ('photo' + photo.suffix.lower())
        if photo.resolve() != photo_path.resolve():
            shutil.copyfile(photo, photo_path)
    candidate = Candidate(profile={'source_text': text, 'facts': []}, source_pdf=str(target), photo=str(photo_path) if photo_path else None)
    session.add(candidate)
    session.add(HumanAction(reason='Review imported source text and confirm structured facts, contact details, graduation date and application declarations'))
    session.commit()
    return candidate

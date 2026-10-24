"""UDESC PDF importer. Keeps page coordinates: no OCR or image quality loss."""
from __future__ import annotations

import re
import pymupdf as fitz
import hashlib
import json
import os
import csv
import io
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path

SUBJECT_RANGES = {
    "morning": [
        (1, 14, "Matemática"), (15, 28, "Biologia"),
        (29, 36, "Língua Estrangeira"), (37, 50, "Português e Literatura"),
    ],
    "afternoon": [
        (1, 14, "Física"), (15, 28, "Química"),
        (29, 37, "História"), (38, 39, "Filosofia"),
        (40, 48, "Geografia"), (49, 50, "Sociologia"),
    ],
}

SUBJECT_RANGES_2025 = {
    **SUBJECT_RANGES,
    "afternoon": [
        (1, 14, "Física"), (15, 28, "Química"),
        (29, 39, "História"), (40, 50, "Geografia"),
    ],
}


@dataclass
class ParsedQuestion:
    session: str
    number: int
    subject: str
    text: str
    clips: list[dict]
    source_page: int


def subject_for(session: str, number: int, year: int | None = None):
    ranges = SUBJECT_RANGES_2025 if year is not None and year <= 2025 else SUBJECT_RANGES
    for start, end, subject in ranges[session]:
        if start <= number <= end:
            return subject
    return None


def headings(page: fitz.Page):
    """Find left-margin 'Questão NN' headings without matching ordinary prose."""
    words = page.get_text("words")
    found = []
    for i, word in enumerate(words):
        x0, y0, x1, y1, token = word[:5]
        if x0 > 105 or not re.fullmatch(r"Quest[aã]o[:.]?", token, re.I):
            continue
        candidates = [w for w in words[i + 1:i + 6]
                      if abs(w[1] - y0) < 6 and w[0] > x0 and w[0] < x0 + 130]
        for candidate in candidates:
            raw = candidate[4].rstrip('.:')
            if raw.isdecimal() and 1 <= int(raw) <= 50:
                found.append((int(raw), max(0, y0 - 4)))
                break
    return sorted(set(found), key=lambda z: z[1])


def _foreign_or_form_page(page: fitz.Page) -> bool:
    top = page.get_text(sort=True)[:420].upper()
    if 'FORMULÁRIO DE' in top or 'FORMULÁRIO DE' in page.get_text(sort=True)[:1000].upper():
        return True
    return ('LÍNGUA ESTRANGEIRA' in top or 'ESPANHOL' in top or
            re.search(r'^\s*(QUESTION|CUESTI[ÓO]N)\s+\d+', top, re.M) is not None)


def _ocr_headings(page: fitz.Page, scale: float = 1.2) -> list[tuple[int, float]]:
    """OCR only the heading strip. Original scanned pixels remain unchanged."""
    if not shutil.which("tesseract"):
        raise ValueError("PDF digitalizado: instale tesseract e tesseract-data-por para importar.")
    region = fitz.Rect(0, 0, min(150, page.rect.width), page.rect.height)
    # OCR only the narrow heading strip, in grayscale. Avoid Tesseract
    # launching multiple OpenMP threads on resource-limited hosting.
    pix = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale), clip=region,
        colorspace=fitz.csGRAY, alpha=False,
    )
    cmd = ["tesseract", "stdin", "stdout", "-l", "por", "--psm", "11", "tsv"]
    env = os.environ.copy()
    env["OMP_THREAD_LIMIT"] = "1"
    timeout = max(10, int(os.getenv("OCR_PAGE_TIMEOUT_SECONDS", "45")))
    try:
        run = subprocess.run(
            cmd, input=pix.tobytes("png"), capture_output=True,
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"OCR excedeu {timeout}s em uma página. "
            "O servidor não tem CPU suficiente para processar esse PDF "
            "nessa requisição. Pré-processe o PDF ou use um worker."
        ) from exc
    if run.returncode:
        raise ValueError("Erro durante OCR: " + run.stderr.decode(errors="replace")[:250])
    rows = [r for r in csv.DictReader(io.StringIO(run.stdout.decode(errors="replace")), delimiter="\t")
            if r.get("text", "").strip()]
    output = []
    for i, row in enumerate(rows):
        if not re.fullmatch(r"Quest[aã]o[:.]?", row["text"], re.I):
            continue
        y = int(row["top"])
        for other in rows[i+1:i+5]:
            if abs(int(other["top"]) - y) > 9:
                continue
            number_text = other["text"].rstrip(".:, ")
            if number_text.isdecimal() and 1 <= int(number_text) <= 50:
                output.append((int(number_text), max(0, y/scale-5)))
                break
    return sorted(set(output), key=lambda z:z[1])


def _known_scanned_edition(path: Path, session: str, year: int | None,
                           page_count: int) -> list[ParsedQuestion] | None:
    """Load verified question positions for exact known scanned official PDFs.

    The SHA-256 protects against applying coordinates to different document
    versions. Original PDFs remain the source of all visible question content.
    """
    layout_path = Path(__file__).with_name("known_scanned_layouts.json")
    if not layout_path.is_file():
        return None
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    layout = json.loads(layout_path.read_text(encoding="utf-8")).get(digest)
    if not layout or layout.get("session") != session or layout.get("page_count") != page_count:
        return None
    # Matching file hash + year guard, so an uploaded edition cannot silently
    # receive question coordinates or answer records from another year.
    if year is None or str(layout.get("edition", "")).split("/")[0] != str(year):
        return None
    if layout.get("page_count") != page_count:
        raise ValueError("PDF incompatível com o mapa de páginas verificado.")
    records = layout["questions"]
    expected = set(range(1, 51)) if session == "afternoon" else (
        set(range(1, 29)) | set(range(37, 51))
    )
    if {r["number"] for r in records} != expected or len(records) != len(expected):
        raise ValueError("Mapa de posições incompleto; importação interrompida.")
    return [ParsedQuestion(
        session=session,
        number=record["number"],
        subject=subject_for(session, record["number"], year),
        text="",  # Original scanned PDFs do not have an embedded text layer.
        clips=record["clips"],
        source_page=record["source_page"],
    ) for record in records]


def parse_proof(path: Path, session: str, year: int | None = None) -> list[ParsedQuestion]:
    """Detect question headings using native PDF text or a scanned-image OCR fallback.

    OCR is used exclusively to locate headings; the displayed question is always
    cropped directly from the original PDF so diagrams and formulas are preserved.
    """
    if session not in SUBJECT_RANGES:
        raise ValueError("Invalid session")
    doc = fitz.open(path)
    try:
        if len(doc) > 250:
            raise ValueError("PDF com páginas demais (limite: 250)")
        known = _known_scanned_edition(path, session, year, len(doc))
        if known is not None:
            return known
        scanned = sum(not bool(page.get_text().strip()) for page in list(doc)[:min(len(doc), 8)]) >= 4
        positions = []
        for page_idx, page in enumerate(doc):
            matches = _ocr_headings(page) if scanned else headings(page)
            for number, top in matches:
                if subject_for(session, number, year) == "Língua Estrangeira":
                    continue
                positions.append((page_idx, top, number))
        if scanned:
            expected = set(range(1,51)) - (set(range(29,37)) if session == "morning" else set())
            have = {n for _, _, n in positions}
            for missing in sorted(expected - have):
                before = [(p, n) for p, _, n in positions if n == missing-1]
                after = [(p, n) for p, _, n in positions if n == missing+1]
                candidates = set()
                for p, _ in before + after:
                    candidates.update((p-1,p,p+1))
                for pidx in sorted(candidates):
                    if pidx < 0 or pidx >= len(doc):
                        continue
                    for n, y in _ocr_headings(doc[pidx], 2.0):
                        if n == missing:
                            positions.append((pidx,y,n))
                            break
                    if any(n == missing for _,_,n in positions):
                        break
        if not positions:
            raise ValueError("Nenhuma questão identificada. Verifique a qualidade do PDF e a instalação do OCR.")
        if scanned:
            expected = set(range(1, 51)) - (set(range(29, 37)) if session == "morning" else set())
            found = {number for _, _, number in positions}
            missing = sorted(expected - found)
            if missing:
                raise ValueError(
                    "OCR não identificou todas as questões. Faltando: " +
                    ", ".join(map(str, missing)) + ". Importação cancelada."
                )
        seen = set()
        positions = sorted(positions)
        result = []
        for idx, (page_idx, top, number) in enumerate(positions):
            if number in seen:
                continue
            seen.add(number)
            # For scanned 2025 papers, English/Spanish and formula pages may
            # separate subjects. Use the next detected heading or stop at the
            # expected end of each subject (especially Q14, Q28 and Q36).
            next_pos = positions[idx+1] if idx+1 < len(positions) else None
            next_page = next_pos[0] if next_pos else len(doc)
            next_top = next_pos[1] if next_pos else None
            clips, text_chunks = [], []
            max_cont_pages = 2
            for pidx in range(page_idx, min(next_page+1, page_idx+max_cont_pages+1, len(doc))):
                page = doc[pidx]
                if not scanned and pidx != page_idx and _foreign_or_form_page(page):
                    break
                if not scanned and pidx != page_idx:
                    txt = page.get_text(sort=True)[:550].upper()
                    if any(w in txt for w in ("LÍNGUA PORTUGUESA E LITERATURA", "REDAÇÃO", "LÍNGUA ESTRANGEIRA", "FOLHA DE RASCUNHO")):
                        break
                # Scanned papers contain section separator/formula pages. Never
                # extend past a section's last question when OCR is enabled.
                if scanned and pidx > page_idx and number in (14, 28, 36, 50):
                    break
                lo = top if pidx == page_idx else (65.0 if scanned else 82.0)
                hi = next_top - 4 if pidx == next_page and next_page != page_idx else page.rect.height-47
                if pidx == page_idx and next_page == page_idx:
                    hi = next_top-4
                hi = min(float(hi),page.rect.height-44)
                if hi-lo < 12:
                    continue
                clip = [25,round(lo,2),round(page.rect.width-25,2),round(hi,2)]
                clips.append({"page":pidx,"rect":clip})
                text_chunks.append(page.get_text(clip=fitz.Rect(clip),sort=True).strip())
                if pidx == next_page:
                    break
            if clips:
                result.append(ParsedQuestion(session,number,subject_for(session,number,year),"\n".join(text_chunks).strip(),clips,page_idx))
        return result
    finally:
        doc.close()


def parse_key(path: Path) -> dict[tuple[str, int], str | None]:
    """Read the official two-band, multi-column UDESC answer-key table.

    Returns (morning|afternoon, question number) -> A-E or None (annulled).
    Ignores the duplicated English/Spanish question ranges.
    """
    doc = fitz.open(path)
    answers = {}
    try:
        # Historical UDESC gabaritos (e.g. 2019/1 and 2024/1) print answers
        # as "01. E" or "1ª Questão (E)". Detect those first and preserve
        # page/section division, including anulações and corrected answers.
        compact_answer = re.compile(
            r"(?<!\d)(\d{1,2})(?:\s*ª\s*Quest[aã]o\s*\(\s*([A-E]|Anulada)\s*\)|\.\s*([A-E]|Anulada)\b)\*?",
            re.IGNORECASE,
        )
        historical = {}
        for page in doc:
            raw = page.get_text(sort=True)
            chunks = re.split(r"(?i)(?:Prova do\s+)?Per[ií]odo\s+Vespertino", raw)
            is_afternoon = bool(re.search(r"(?i)(?:Prova do\s+)?Per[ií]odo\s+Vespertino", raw))
            for chunk_idx, chunk in enumerate(chunks):
                session = "afternoon" if is_afternoon and chunk_idx > 0 else "morning"
                for line in chunk.splitlines():
                    for match in compact_answer.finditer(line):
                        number = int(match.group(1))
                        if session == "morning" and 29 <= number <= 36:
                            continue  # English / Spanish duplicate question numbers
                        if session == "afternoon" and not 1 <= number <= 50:
                            continue
                        answer = (match.group(2) or match.group(3)).upper()
                        historical[(session, number)] = None if answer.startswith("ANULAD") else answer
        if len(historical) == 92 and sum(x is None for x in historical.values()) <= 92:
            return historical
        # Modern table layout (2025/1 onward): retain the original reader.
        for page in doc:
            words = page.get_text("words")
            raw = page.get_text()
            # Split by the printed section header's vertical position when possible.
            afternoon_tokens = [w[1] for w in words if w[4].lower().strip() == 'vespertino']
            separator = min(afternoon_tokens) if afternoon_tokens else page.rect.height * .47
            # Look for numeric words followed by a response in the same row.
            for w in words:
                x, y, _, _, token = w[:5]
                if not token.isdecimal():
                    continue
                num = int(token)
                if not 1 <= num <= 50:
                    continue
                session = 'morning' if y < separator else 'afternoon'
                subject = subject_for(session, num)
                if not subject or subject == 'Língua Estrangeira':
                    continue
                # A column containing table question numbers, not its answer columns.
                if not (55 < x < 95 or 148 < x < 190 or 247 < x < 285 or
                        341 < x < 385 or 434 < x < 480):
                    continue
                matching = [other for other in words
                            if other[0] > w[2] and other[0] - w[2] < 57
                            and abs(other[1] - y) < 5 and
                            re.fullmatch(r'(?:[A-E]|Anulad[ao])', other[4], re.I)]
                if matching:
                    answer = min(matching, key=lambda z: z[0])[4]
                    answers[(session, num)] = None if answer.lower().startswith('anulad') else answer.upper()
        return answers
    finally:
        doc.close()

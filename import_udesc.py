#!/usr/bin/env python3
"""Import UDESC PDF editions from your computer directly into Neon PostgreSQL and Cloudflare R2.

Single-file tool compatible with the cloud edition of UDESC Simulados.
Reads DATABASE_URL, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
R2_BUCKET_NAME and optional R2_ENDPOINT from the project's .env.

Examples:
    python import_udesc.py --label 2025/1 --morning morning.pdf --afternoon afternoon.pdf --key answers.pdf
    python import_udesc.py --label 2025/1 --morning morning.pdf --afternoon afternoon.pdf --key answers.pdf --precache-images
    python import_udesc.py --label 2026/1 --morning morning.pdf --key answers.pdf --dry-run

Dependencies:
    pip install "psycopg[binary]" boto3 python-dotenv pymupdf
    sudo pacman -S tesseract tesseract-data-por   # only for unknown scanned PDFs

The bundled position map recognizes the exact official scanned UDESC 2025/1 PDFs
that were previously validated (by SHA-256) without using OCR.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import pymupdf as fitz

# Validated page positions of two official 2025/1 scanned PDFs, compressed to
# keep this program truly self-contained (no JSON file beside it).
_KNOWN_LAYOUTS_PACKED = (
    "c-rM$+in~;41M=k7=AXk;zhUqN>K!}v*W-(W2e461^M@`ouu_8nrgWeEjf9!20IxYQWAN@!!IYNr)MrsbG&d%!W~Sw-La1|"
    "&$J8VDTHaB{WfEuc(EnCNHOeqmve$;M%Ye}@$C20FZ=iG-?Z#5?=G*e_5)|=(EkQ!=ieUOF1NSeAAGvLxw^di_~Xgv>ErTY"
    "zP`V@+dt&=ef019<@VuCxBJ0A9*0i9{~wQ!ukJr>mz(`FIR9_(_WJ&2UOqg$aQ_6KJemKx{QUGy&)@d%XFUJa<M7Qg-@W+Z"
    "!$0)rFGhgkCd8Mc-iM8QImj+<+_SMipZxXp<mKqs^A!NF7XTRG^;Ca4|46hp|MUO-Mv{JG@RgsK{N9*aCZeK=NMs`M^i!^N"
    "QA(iaqTI{;DS0aw<@PQb6<yR9O~fJ-iKm~T+C>RBL|OoZUH~8p`)O4n;GO0!6?;LzyXtM~=w<51TBfOJBQLU%Z@)}A2E}EX"
    "djWuxt6io?J82lpNx>D36huY}?Y$J)>%}_&y#RomD?Px2staH*2zUgu!z2#2PRn^o6d5TRoF=Rp@&NaO0JB!ib07lc!2j7U"
    "Ru93`pB>1cV60pQ1X&S+&<_NJR0%-<%>yg;13`dnm7@bT->@07=6+R<|3Sv~$sypqt6Ckb(4O$4ph$Q@IZXi><t0aef*I!p"
    "gOOfXPE<g~d4K`$6<E<z5&D53u$`y2=Y5bhFL}!O6v&7VIRfa-?s=d+^Fdy9M=7UNAWMAZ5Ime{)mutZ<miz50U>xaqv@c1"
    "{*T}^4YiW|$?{)01PEMfC`zi{8u|f&vZ1%8!Gg7w95u=8lni*~M4A|+elWlk--NNEvLB!f159$(&ThS8ZckQtO*@r+_32a&"
    "v6C19_R*{~$+ZtzNs;=aGF9<(Du-0(IC<%V8TCid-b_(30pv1OK{AF`j=*DWSY=+J2?6v2L9ok#sXa-3{5R5Au_Tx26cUys"
    "Qu|o)cry*g&+<y)tc;u_9>GlQ$;l8jc?4c5@RhNXM?etREF>obdNOO?A99)SAsI6#hk%{k{UOD{cw{b<QY0g#<PaRdBOh<5"
    "!AL1ClMy5%BPdyv<Z6cHsUHY9*@flp=@@dFJU6c-`Z7L74grB3Z^mvgB~CfhXBi<QM?fv;eGS3MkG~$lILj0!w@j0pW0-LY"
    "08`=#mjy2%&D$OA0KzT?oaSZc^94^a&s+B1W%ARqJ4}H(UED1<SJ(SLeiaQWyxJ;BvyiqQ5h~?c1R~+aQ&%!n_=ybFy+;+Q"
    "GqMm5Wg+fGfmdyz2POME6rnyy+0AwlKa`J#UIcj6K8l*tkG)JEtLLw)67EKUT2=`+Sfio&8ilNwljm;qhYrln`Z&}+x+0P_"
    "mY&sbq@ES|h!v5nF#zaOfsJV3rK!1>B4`TkSCqUmc28b#hOX93o-~=W6`iax1in~r*1%J<!I`Id3%MfZmGPTm03frLne^1W"
    "*7`u8+M}Pdw^8$&OC+-$(*a|VQEQ50YlUQW0Hk{%vnJ*>7l}gk!Qxf5_Z<mkeMx)YVzrJ%O!g#{tzWIG(M3-}S<;Iy-a;z-"
    "+ETz9{~o2X1Zm@~>=ee@QaVz~9zdlko_#&ZWIY(3ePa=IJ&9!N&py=b5z&)E7HfSVkS1HXraraCAdn$n3GE3>i11D|vJk!2"
    "9o2y#l=CW-6_KHnw{@#^1on!%7P)>PVDHV2SA?;ul2(f6WU4+n0QF@rthr+m_5;DU{+d><ietHIIb}g%nIpD$)u72)688fE"
    "N5ji7Y)`L~)0BEqIc-51vx-Ns%VfBzj76<dIk`d^uZTQWN!?mQ7i$F2ur?XTD*RmQ=OW`(v3>0)Ma?^am0l_45KuI{1Hks&"
    "vcgBU=M@3Tq@@eWC?z=p#n$UTNqsaMnyvJ7F~@*88b&E;EM*Gz({#=9h|XhKiTY`|_C{63E3HV(AvmC}*sJz+U1{@S4uS{6"
    "I7PqtUfcB=`suxQK<=m7l_2&50cJx&iN>Po*SdUIH{Yu-*FZFCpkWPx$6)BHW6|_04Kzr`Q4_v-th&}f<K*kfKP*i@t^"
)

@lru_cache(maxsize=1)
def _get_known_layouts() -> dict:
    return json.loads(zlib.decompress(base64.b85decode(_KNOWN_LAYOUTS_PACKED)).decode("utf-8"))


# --- PDF parser (same question/clip data model as the cloud application's parser) ---

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
    ranges = SUBJECT_RANGES_2025 if year == 2025 else SUBJECT_RANGES
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
    env.setdefault("OMP_THREAD_LIMIT", "1")
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
    """Use verified question clips for the exact official scanned 2025/1 PDFs.

    Hash verification prevents accidentally using coordinates from another PDF.
    The mapping stores positions only; the uploaded PDF is still preserved and
    used for displaying each question and its figures.
    """
    if year != 2025:
        return None
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    layout = _get_known_layouts().get(digest)
    if not layout or layout.get("edition") != "2025/1" or layout.get("session") != session:
        return None
    if layout.get("page_count") != page_count:
        raise ValueError("PDF 2025/1 incompatível com o mapa de páginas verificado.")
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


# --- Standalone NeonDB and Cloudflare R2 importer ---

def _load_env(file_path: Path | None) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError('Install python-dotenv: pip install python-dotenv') from exc
    paths = [file_path] if file_path else [Path.cwd() / '.env', Path(__file__).resolve().parent / '.env']
    for path in paths:
        if path and path.is_file():
            load_dotenv(path, override=False)
            print(f"Using environment configuration: {path}")
            return
    if file_path:
        raise ValueError(f'.env not found: {file_path}')
    print('No .env file found; using process environment variables.')


def _required(name: str) -> str:
    value = os.getenv(name, '').strip()
    if not value:
        raise ValueError(f'Missing required environment variable: {name}')
    return value


def _validate_pdf(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f'File not found: {path}')
    if path.suffix.lower() != '.pdf':
        raise ValueError(f'Expected PDF file: {path}')
    with fitz.open(path) as doc:
        if not doc.is_pdf or doc.needs_pass or not len(doc):
            raise ValueError(f'Invalid or encrypted PDF: {path}')
        if len(doc) > 250:
            raise ValueError(f'PDF exceeds 250 pages: {path}')


def _r2_client():
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise RuntimeError('Install boto3: pip install boto3') from exc
    endpoint = os.getenv('R2_ENDPOINT', '').strip() or f"https://{_required('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com"
    if not endpoint.startswith('https://'):
        raise ValueError('R2_ENDPOINT must use HTTPS')
    return boto3.client('s3', endpoint_url=endpoint.rstrip('/'), region_name='auto',
                        aws_access_key_id=_required('R2_ACCESS_KEY_ID'),
                        aws_secret_access_key=_required('R2_SECRET_ACCESS_KEY'),
                        config=Config(signature_version='s3v4', retries={'max_attempts': 3, 'mode': 'standard'},
                                      connect_timeout=15, read_timeout=90))


def _open_db():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError('Install psycopg: pip install "psycopg[binary]"') from exc
    url = _required('DATABASE_URL')
    if not url.startswith(('postgresql://', 'postgres://')):
        raise ValueError('DATABASE_URL must be a PostgreSQL URL')
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=20)


def _check_schema_and_duplicate(label: str, owner_id: int) -> None:
    with _open_db() as conn:
        if conn.execute("SELECT to_regclass('public.editions') AS table_name").fetchone()['table_name'] is None:
            raise ValueError('Database schema not found. Run the project PostgreSQL migrations first.')
        if conn.execute('SELECT id FROM editions WHERE label=%s AND user_id=%s', (label,owner_id)).fetchone():
            raise ValueError(f'Edition {label!r} already exists. Nothing was overwritten.')


def _check_questions(questions: list[ParsedQuestion], roles: dict[str, Path],
                     key_data: dict[tuple[str, int], str | None] | None,
                     allow_partial: bool) -> list[str]:
    warnings = []
    for session in ('morning','afternoon'):
        if session not in roles:
            continue
        numbers = [q.number for q in questions if q.session == session]
        expected = (set(range(1,29)) | set(range(37,51))) if session == 'morning' else set(range(1,51))
        actual = set(numbers)
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        duplicates = sorted({number for number in numbers if numbers.count(number) > 1})
        if missing or extra or duplicates:
            issue = f'{session}: missing={missing}, unexpected={extra}, duplicate={duplicates}'
            if not allow_partial:
                raise ValueError('Question validation failed: ' + issue + '. Use --allow-partial only after checking the PDF.')
            warnings.append(issue)
        if any(not q.clips for q in questions if q.session == session):
            raise ValueError(f'Found question without page clips in {session}')
    if key_data is not None:
        missing_answers = [(q.session,q.number) for q in questions if (q.session,q.number) not in key_data]
        if missing_answers:
            issue = f'Answer key missing {len(missing_answers)} entries: {missing_answers[:15]}'
            if not allow_partial:
                raise ValueError(issue + '. Use --allow-partial if the answer key is incomplete.')
            warnings.append(issue)
    else:
        warnings.append('No answer key supplied; questions will be imported without correct answers.')
    return warnings


def _precache_images(client, bucket: str, parsed: list[ParsedQuestion],
                     question_ids: dict[tuple[str,int], int], document_ids: dict[str,int],
                     files: dict[str,Path]) -> tuple[int,int]:
    """Create precisely the clip cache keys consumed by app.py's /render/{index} route."""
    created = 0
    failed = 0
    docs = {}
    try:
        docs = {role: fitz.open(path) for role,path in files.items() if role in ('morning','afternoon')}
        for q in parsed:
            pdf = docs[q.session]
            for idx, clip in enumerate(q.clips):
                try:
                    page = clip['page']
                    coords = clip['rect']
                    question_id = question_ids[(q.session,q.number)]
                    fingerprint = hashlib.sha256(json.dumps([
                        f'clip:{question_id}:{idx}',page,1.7,coords
                    ]).encode()).hexdigest()[:24]
                    object_key = f'cache/{document_ids[q.session]}/{fingerprint}.png'
                    png = pdf[page].get_pixmap(matrix=fitz.Matrix(1.7, 1.7), clip=fitz.Rect(coords),
                                               alpha=False).tobytes('png')
                    client.put_object(Bucket=bucket, Key=object_key, Body=png, ContentType='image/png')
                    created += 1
                except Exception as exc:
                    failed += 1
                    print(f'WARN: cache failed for {q.session} question {q.number}, clip {idx}: {exc}',file=sys.stderr)
    finally:
        for doc in docs.values():
            doc.close()
    return created, failed


def import_edition(label: str, files: dict[str,Path], dry_run: bool, allow_partial: bool,
                   precache_images: bool, owner_email: str | None = None) -> dict:
    year = int(label[:4]) if label[:4].isdigit() else None
    for role,path in files.items():
        print(f'Checking {role} PDF: {path}')
        _validate_pdf(path)
    parsed = []
    for session in ('morning','afternoon'):
        if session in files:
            print(f'Extracting {session} question locations...')
            result = parse_proof(files[session], session, year=year)
            parsed.extend(result)
            print(f'  {len(result)} questions; {sum(len(q.clips) for q in result)} clips')
    key_data = parse_key(files['key']) if 'key' in files else None
    warnings = _check_questions(parsed, files, key_data, allow_partial)
    graded = sum((q.session,q.number) in key_data and key_data[(q.session,q.number)] is not None
                 for q in parsed) if key_data is not None else 0
    annulled = sum((q.session,q.number) in key_data and key_data[(q.session,q.number)] is None
                    for q in parsed) if key_data is not None else 0
    summary = {'edition': label, 'questions': len(parsed), 'correct_answers': graded,
               'annulled': annulled, 'warnings': warnings, 'dry_run': dry_run}
    if dry_run:
        print('DRY RUN: no R2 uploads or NeonDB changes.')
        return summary

    if not owner_email:
        raise ValueError('Set --owner-email to an email account already registered on the website.')
    with _open_db() as conn:
        user = conn.execute('SELECT id FROM users WHERE email=%s', (owner_email.strip().lower(),)).fetchone()
    if not user:
        raise ValueError('Account not found; create the account on the website first.')
    owner_id = user['id']
    _check_schema_and_duplicate(label, owner_id)
    bucket = _required('R2_BUCKET_NAME')
    client = _r2_client()
    uid = uuid.uuid4().hex
    remote_keys = {}
    saved_keys = []
    committed = False
    question_ids = {}
    document_ids = {}
    try:
        for role,path in files.items():
            key = f'users/{owner_id}/editions/{uid}/{role}.pdf'
            print(f'Uploading {role} PDF to R2...')
            client.upload_file(str(path),bucket,key,ExtraArgs={'ContentType':'application/pdf'})
            remote_keys[role] = key
            saved_keys.append(key)

        print('Writing edition and questions to NeonDB...')
        with _open_db() as conn:
            with conn.transaction():
                edition_id = conn.execute("""INSERT INTO editions(label,imported_at,warning,user_id)
                    VALUES(%s,%s,%s,%s) RETURNING id""",
                    (label,datetime.now(timezone.utc).isoformat(timespec='seconds'),
                     ' | '.join(warnings),owner_id)).fetchone()['id']
                for role,path in files.items():
                    document_ids[role] = conn.execute("""INSERT INTO documents(edition_id,role,path,filename)
                        VALUES(%s,%s,%s,%s) RETURNING id""",
                        (edition_id,role,remote_keys[role],path.name)).fetchone()['id']
                for q in parsed:
                    identity = (q.session,q.number)
                    answer_found = key_data is not None and identity in key_data
                    correct = key_data[identity] if answer_found else None
                    question_ids[identity] = conn.execute("""INSERT INTO questions(
                        edition_id,document_id,session,number,subject,text_content,clips_json,
                        source_page,correct_answer,annulled
                    ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (edition_id,document_ids[q.session],q.session,q.number,q.subject,q.text,
                     json.dumps(q.clips,ensure_ascii=False),q.source_page,correct,
                     int(answer_found and correct is None))).fetchone()['id']
        committed = True
        summary['edition_id'] = edition_id
        summary['dry_run'] = False
    except BaseException:
        if not committed:
            for key in saved_keys:
                try:
                    client.delete_object(Bucket=bucket, Key=key)
                except Exception as exc:
                    print(f'WARN: unable to remove orphaned R2 object {key}: {exc}',file=sys.stderr)
        raise
    if precache_images:
        print('Pre-rendering and uploading question PNGs to R2...')
        summary['precache_ok'], summary['precache_failed'] = _precache_images(
            client,bucket,parsed,question_ids,document_ids,files)
    return summary


def main() -> int:
    cli = argparse.ArgumentParser(description='Import UDESC PDF exams locally into NeonDB + Cloudflare R2')
    cli.add_argument('--owner-email',help='Required for writes: registered website account that will own the edition')
    cli.add_argument('--label',required=True,help='Edition label, e.g. 2025/1')
    cli.add_argument('--morning',type=Path,help='Official morning exam PDF')
    cli.add_argument('--afternoon',type=Path,help='Official afternoon exam PDF')
    cli.add_argument('--key',type=Path,help='Official answer key PDF')
    cli.add_argument('--env',type=Path,help='Path to your existing .env (default: current directory or script directory)')
    cli.add_argument('--dry-run',action='store_true',help='Validate PDFs, questions and keys without cloud access')
    cli.add_argument('--allow-partial',action='store_true',help='Allow incomplete question detection or answer keys')
    cli.add_argument('--precache-images',action='store_true',help='Pre-render question PNGs on this computer and store them in R2')
    args = cli.parse_args()
    if not re.fullmatch(r'[\w. /-]{3,40}',args.label.strip(), re.UNICODE):
        cli.error('Invalid --label; example: 2025/1')
    if args.morning is None and args.afternoon is None:
        cli.error('Provide --morning and/or --afternoon')
    files = {role:path.expanduser().resolve() for role,path in
             (('morning',args.morning),('afternoon',args.afternoon),('key',args.key)) if path is not None}
    try:
        _load_env(args.env.expanduser().resolve() if args.env else None)
        result = import_edition(args.label.strip(),files,args.dry_run,args.allow_partial,args.precache_images,args.owner_email)
        print(json.dumps(result,indent=2,ensure_ascii=False))
        return 0
    except (ValueError,RuntimeError,FileNotFoundError) as exc:
        print('ERROR: '+str(exc),file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Interrupted.',file=sys.stderr)
        return 130
    except Exception as exc:
        print(f'ERROR ({type(exc).__name__}): {exc}',file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

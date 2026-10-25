"""UDESC Simulados: PostgreSQL + private Cloudflare R2 storage."""
from __future__ import annotations

import hashlib
import asyncio
import hmac
import json
import logging
import os
import random
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pymupdf as fitz
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from parser import parse_key, parse_proof
from ufsc import parse_key as parse_ufsc_key, parse_proof as parse_ufsc_proof, validate_identity as validate_ufsc_identity
from ufsc_score import grade as grade_objective
from db import database
from settings import ROOT
from storage import upload_file, upload_bytes, get_bytes, get_optional_bytes, delete_object, delete_prefix
from auth import (COOKIE_NAME, create_session, current_session, token_digest,
                  issue_cookie, clear_cookie, normalize_email, validate_password,
                  hash_password, verify_password, user_id)
from psycopg.errors import UniqueViolation

MAX_PDF_BYTES = int(os.getenv('MAX_PDF_MB', '35')) * 1024 * 1024
app = FastAPI(title='Vestibulares UDESC + UFSC', docs_url=None, redoc_url=None, openapi_url=None)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')

PUBLIC_API = {'/api/health', '/api/auth/register', '/api/auth/login'}
SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


@app.middleware('http')
async def authenticate(request: Request, call_next):
    """Authenticate every private API, check CSRF and enforce same-origin browser writes."""
    path = request.url.path
    if request.method not in SAFE_METHODS:
        from urllib.parse import urlsplit
        origin = request.headers.get('origin')
        if origin:
            parts = urlsplit(origin)
            if parts.scheme not in ('http', 'https') or parts.netloc.lower() != request.headers.get('host', '').lower():
                return JSONResponse({'detail': 'Origem não permitida'}, status_code=403)
    if path.startswith('/api/') and path not in PUBLIC_API:
        session = await asyncio.to_thread(current_session, request)
        if session is None:
            return JSONResponse({'detail': 'Faça login para continuar'}, status_code=401,
                                headers={'Cache-Control': 'no-store'})
        request.state.user_id = session['user_id']
        request.state.user_email = session['email']
        request.state.csrf_token = session['csrf_token']
        if request.method not in SAFE_METHODS and not hmac.compare_digest(
                request.headers.get('x-csrf-token', ''), session['csrf_token']):
            return JSONResponse({'detail': 'Token CSRF inválido'}, status_code=403,
                                headers={'Cache-Control': 'no-store'})
    response = await call_next(request)
    if path == '/' or path.startswith('/api/auth/') or (path.startswith('/api/') and path not in PUBLIC_API):
        response.headers['Cache-Control'] = 'no-store'
    return response


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


@app.post('/api/auth/register')
def register(credentials: Credentials):
    email = normalize_email(credentials.email)
    validate_password(credentials.password)
    password_hash = hash_password(credentials.password)
    try:
        with database() as db:
            row = db.execute('INSERT INTO users(email,password_hash) VALUES(%s,%s) RETURNING id',
                             (email, password_hash)).fetchone()
    except UniqueViolation:
        raise HTTPException(409, 'Este e-mail já está cadastrado')
    token, csrf, expires = create_session(row['id'])
    response = JSONResponse({'user': {'id': row['id'], 'email': email}, 'csrf_token': csrf})
    issue_cookie(response, token, expires)
    return response


@app.post('/api/auth/login')
def login(credentials: Credentials):
    email = normalize_email(credentials.email)
    with database() as db:
        row = db.execute('SELECT id,email,password_hash FROM users WHERE email=%s', (email,)).fetchone()
    # Verify even for nonexistent addresses to reduce account enumeration by timing.
    dummy = 'pbkdf2_sha256$600000$' + '00'*16 + '$' + '00'*32
    if not verify_password(credentials.password, row['password_hash'] if row else dummy) or not row:
        raise HTTPException(401, 'E-mail ou senha incorretos')
    token, csrf, expires = create_session(row['id'])
    response = JSONResponse({'user': {'id': row['id'], 'email': row['email']}, 'csrf_token': csrf})
    issue_cookie(response, token, expires)
    return response


@app.get('/api/auth/me')
def me(request: Request):
    return {'user': {'id': request.state.user_id, 'email': request.state.user_email},
            'csrf_token': request.state.csrf_token}


@app.post('/api/auth/logout')
def logout(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        with database() as db:
            db.execute('DELETE FROM user_sessions WHERE token_hash=%s', (token_digest(token),))
    response = JSONResponse({'ok': True})
    clear_cookie(response)
    return response


@app.get('/api/health')
def health():
    return {'ok': True}


def get_institution(value):
    if value not in ('UDESC','UFSC'):
        raise HTTPException(422, 'Vestibular deve ser UDESC ou UFSC')
    return value

def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def safe_delete(key: str):
    try:
        delete_object(key)
    except Exception:
        logging.exception('Failed deleting R2 object; cleanup required: %s', key)


def safe_delete_cache(prefix: str):
    try:
        delete_prefix(prefix)
    except Exception:
        logging.exception('Failed deleting rendered R2 cache under %s', prefix)


def _document_key(db, document_id: int, owner_id: int) -> str:
    row = db.execute('''SELECT d.path FROM documents d JOIN editions e ON e.id=d.edition_id
        WHERE d.id=%s AND e.user_id=%s''', (document_id,owner_id)).fetchone()
    if not row:
        raise HTTPException(404, 'PDF não encontrado')
    return row['path']


def row_dict(row):
    return dict(row) if row else None


def get_question(db, question_id, owner_id):
    row = db.execute('''SELECT q.*, e.label AS edition, e.institution, e.booklet_color, d.role AS source_role
                        FROM questions q JOIN editions e ON q.edition_id=e.id
                        JOIN documents d ON q.document_id=d.id WHERE q.id=%s AND e.user_id=%s''', (question_id, owner_id)).fetchone()
    if not row:
        raise HTTPException(404, 'Questão não encontrada')
    return dict(row)


def _reference_metadata(q):
    """Expose safe labels/counts, not raw storage keys or arbitrary PDF coordinates."""
    raw = q.get('reference_clips_json') or '[]'
    try:
        refs = json.loads(raw)
    except (ValueError, TypeError):
        refs = []
    return [{'label': ref.get('label', 'Texto de apoio'),
             'clips': len(ref.get('clips', []))} for ref in refs]


def question_payload(q):
    return {'id': q['id'], 'edition_id': q['edition_id'], 'edition': q['edition'],
            'institution':q['institution'],'booklet_color':q['booklet_color'],
            'subject': q['subject'], 'session': q['session'], 'number': q['number'],
            'source_page': q['source_page'] + 1, 'clips': len(json.loads(q['clips_json'])),
            'references': _reference_metadata(q),
            'difficulty': q['difficulty'], 'correct_count': q['correct_count'],
            'answer_kind': q.get('answer_kind','choice'), 'numeric_key': q.get('numeric_key'),
            'proposition_count': q.get('proposition_count'),
            'annulled': bool(q['annulled']),
            'has_answer': q['correct_answer'] is not None or q.get('numeric_key') is not None or bool(q['annulled']),
            'text': q['text_content']}


@app.get('/')
def home():
    return FileResponse(ROOT / 'static/index.html')


@app.get('/api/dashboard')
def dashboard(request: Request, institution: str = 'UDESC'):
    institution = get_institution(institution)
    with database() as db:
        totals = {
            'editions': db.execute('SELECT COUNT(*) AS count FROM editions WHERE user_id=%s AND institution=%s', (user_id(request),institution)).fetchone()['count'],
            'questions': db.execute('SELECT COUNT(*) AS count FROM questions q JOIN editions e ON e.id=q.edition_id WHERE e.user_id=%s AND e.institution=%s', (user_id(request),institution)).fetchone()['count'],
            'answered_keys': db.execute('SELECT COUNT(*) AS count FROM questions q JOIN editions e ON e.id=q.edition_id WHERE e.user_id=%s AND e.institution=%s AND (q.correct_answer IS NOT NULL OR q.numeric_key IS NOT NULL OR q.annulled=1)', (user_id(request),institution)).fetchone()['count'],
            'notes': db.execute("SELECT COUNT(*) AS count FROM notes n JOIN questions q ON q.id=n.question_id JOIN editions e ON e.id=q.edition_id WHERE e.user_id=%s AND e.institution=%s AND TRIM(n.body)<>''", (user_id(request),institution)).fetchone()['count'],
            'completed': db.execute("SELECT COUNT(*) AS count FROM attempts WHERE status='completed' AND user_id=%s AND institution=%s", (user_id(request),institution)).fetchone()['count'],
        }
        history = [dict(r) for r in db.execute('''SELECT a.id, a.title, a.completed_at, a.institution,a.objective_points,a.objective_max,
            SUM(CASE WHEN i.snapshot_annulled=0 AND (i.snapshot_correct IS NOT NULL OR i.snapshot_numeric IS NOT NULL) THEN 1 ELSE 0 END) AS graded,
            SUM(CASE WHEN i.snapshot_annulled=0 AND ((i.snapshot_kind='choice' AND i.selected_answer=i.snapshot_correct AND i.snapshot_correct IS NOT NULL) OR (i.snapshot_kind IN ('sum','open') AND i.selected_numeric=i.snapshot_numeric AND i.snapshot_numeric IS NOT NULL)) THEN 1 ELSE 0 END) AS correct
            FROM attempts a JOIN attempt_items i ON i.attempt_id=a.id
            WHERE a.status='completed' AND a.user_id=%s AND a.institution=%s GROUP BY a.id ORDER BY a.completed_at DESC LIMIT 8''', (user_id(request),institution))]
        subjects = [dict(r) for r in db.execute('SELECT q.subject, COUNT(*) AS count FROM questions q JOIN editions e ON e.id=q.edition_id WHERE e.user_id=%s AND e.institution=%s GROUP BY q.subject ORDER BY count DESC,q.subject', (user_id(request),institution))]
    return {'totals': totals, 'history': history, 'subjects': subjects}


@app.get('/api/editions')
def editions(request: Request, institution: str = 'UDESC'):
    institution = get_institution(institution)
    with database() as db:
        return [dict(r) for r in db.execute('''SELECT e.*, COUNT(DISTINCT q.id) AS questions,
          COUNT(DISTINCT d.id) AS files,
          COUNT(DISTINCT CASE WHEN q.correct_answer IS NOT NULL OR q.numeric_key IS NOT NULL OR q.annulled=1 THEN q.id END) AS keys
          FROM editions e LEFT JOIN questions q ON q.edition_id=e.id
          LEFT JOIN documents d ON d.edition_id=e.id
          WHERE e.user_id=%s AND e.institution=%s GROUP BY e.id ORDER BY e.label DESC''', (user_id(request),institution))]


async def save_uploaded(upload: UploadFile | None, target: Path):
    if upload is None:
        return None
    if not (upload.filename or '').lower().endswith('.pdf'):
        raise HTTPException(400, 'Envie arquivos PDF (.pdf)')
    # stream in chunks to enforce size limit
    total = 0
    with target.open('wb') as f:
        while chunk := await upload.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_PDF_BYTES:
                raise HTTPException(413, f'PDF maior que {MAX_PDF_BYTES//(1024*1024)} MB')
            f.write(chunk)
    try:
        with fitz.open(target) as d:
            if not d.is_pdf or d.needs_pass:
                raise ValueError('arquivo PDF protegido')
    except Exception:
        raise HTTPException(400, f'PDF inválido ou protegido: {upload.filename}')
    return target


@app.post('/api/editions/import')
async def import_edition(request: Request, label: str = Form(...), morning: UploadFile | None = File(None),
                         afternoon: UploadFile | None = File(None), key: UploadFile | None = File(None),
                         institution: str = Form('UDESC'), color: str = Form('standard'),
                         key_morning: UploadFile | None = File(None), key_afternoon: UploadFile | None = File(None)):
    institution = get_institution(institution)
    label = label.strip()
    color = color.strip().lower()
    if not re.fullmatch(r'[a-z]{3,20}',color):
        raise HTTPException(422, 'Cor da prova inválida')
    if institution=='UFSC' and key is not None:
        raise HTTPException(422,'UFSC exige gabaritos separados por Prova 1 e Prova 2')
    if institution=='UFSC' and (key_morning is None and morning is not None or key_afternoon is None and afternoon is not None):
        raise HTTPException(422, 'UFSC: envie um gabarito correspondente a cada dia de prova')
    if not re.fullmatch(r'[\w. /-]{3,40}', label, re.UNICODE):
        raise HTTPException(422, 'Identificação inválida (ex.: 2026/1)')
    if morning is None and afternoon is None:
        raise HTTPException(422, 'Envie pelo menos uma prova')
    with database() as db:
        if db.execute('SELECT 1 FROM editions WHERE label=%s AND user_id=%s AND institution=%s AND booklet_color=%s', (label,user_id(request),institution,color)).fetchone():
            raise HTTPException(409, 'Essa edição já existe; exclua antes de reimportar')
    # Parse from temporary local files. Persist only inside a private R2 bucket.
    upload_id = uuid.uuid4().hex
    uploaded = {}
    r2_keys = []
    with tempfile.TemporaryDirectory(prefix='udesc-upload-') as tmp:
        tmpdir = Path(tmp)
        try:
            for role, f in [('morning', morning), ('afternoon', afternoon), ('key', key),
                            ('key_morning',key_morning), ('key_afternoon',key_afternoon)]:
                if f is not None:
                    target = tmpdir / (role + ('.html' if role.startswith('key_') and (f.filename or '').lower().endswith(('.html','.htm')) else '.pdf'))
                    if institution=='UFSC' and role.startswith('key_') and (f.filename or '').lower().endswith(('.html','.htm')):
                        data=await f.read(MAX_PDF_BYTES+1)
                        if len(data)>MAX_PDF_BYTES: raise HTTPException(413,'Gabarito HTML muito grande')
                        target.write_bytes(data)
                    else:
                        await save_uploaded(f, target)
                    uploaded[role] = (f.filename, target)
            parsed = []
            year = int(label[:4]) if label[:4].isdigit() else None
            for session in ('morning', 'afternoon'):
                if session in uploaded:
                    parsed.extend(await asyncio.to_thread(parse_ufsc_proof,uploaded[session][1],session) if institution=='UFSC'
                                  else await asyncio.to_thread(parse_proof,uploaded[session][1],session,year=year))
            answer_key = {}
            if institution=='UFSC':
                for day in ('morning','afternoon'):
                    if 'key_'+day in uploaded:
                        answer_key.update({(day,n):v for n,v in
                            (await asyncio.to_thread(parse_ufsc_key,uploaded['key_'+day][1],day)).items()})
            elif 'key' in uploaded:
                answer_key = await asyncio.to_thread(parse_key,uploaded['key'][1])
            if institution=='UFSC':
                for role,(filename,path) in uploaded.items():
                    if role=='key': continue
                    day=role.replace('key_','')
                    await asyncio.to_thread(validate_ufsc_identity,path,day,color,year)
            if not parsed:
                raise HTTPException(422, 'Nenhuma questão detectada; confira PDF e OCR/Tesseract.')
            warnings = []
            for session in ('morning', 'afternoon'):
                if session in uploaded:
                    found = sorted(q.number for q in parsed if q.session == session)
                    expected = (list(range(1,41)) if session=='afternoon' else list(range(1,13))+list(range(21,41))) if institution=='UFSC' else (list(range(1,51)) if session=='afternoon' else list(range(1,29))+list(range(37,51)))
                    if found != expected:
                        warnings.append(f'{session}: {len(found)} questões; numeração incompleta ou duplicada')
            # Upload originals BEFORE DB transaction; rollback and remove on error.
            for role, (filename, path) in uploaded.items():
                remote_key = f'users/{user_id(request)}/editions/{upload_id}/{role}{path.suffix}'
                await asyncio.to_thread(upload_file, path, remote_key,'text/html' if path.suffix=='.html' else 'application/pdf')
                r2_keys.append(remote_key)
            found_answers = 0
            annulled_count = 0
            with database() as db:
                edition_id = db.execute("""INSERT INTO editions(label,imported_at,warning,user_id,institution,booklet_color)
                    VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (label, utcnow(), ' | '.join(warnings),user_id(request),institution,color)).fetchone()['id']
                docids = {}
                for role, (filename, path) in uploaded.items():
                    docids[role] = db.execute("""INSERT INTO documents(edition_id,role,path,filename)
                        VALUES(%s,%s,%s,%s) RETURNING id""",
                        (edition_id, role, f'users/{user_id(request)}/editions/{upload_id}/{role}{path.suffix}', filename)).fetchone()['id']
                for q in parsed:
                    has_key = (q.session, q.number) in answer_key
                    answer = answer_key.get((q.session, q.number))
                    found_answers += bool(answer) if institution=='UDESC' else bool(has_key and not answer['annulled'])
                    annulled_count += bool(has_key and answer is None) if institution=='UDESC' else bool(has_key and answer['annulled'])
                    db.execute("""INSERT INTO questions(edition_id,document_id,session,number,subject,
                        text_content,clips_json,source_page,correct_answer,annulled,answer_kind,numeric_key,proposition_count,reference_clips_json)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (edition_id, docids[q.session], q.session, q.number, q.subject, q.text,
                         json.dumps(q.clips, ensure_ascii=False), q.source_page,
                         (answer if institution=='UDESC' else None),
                         int(has_key and answer is None) if institution=='UDESC' else int(has_key and answer['annulled']),
                         'choice' if institution=='UDESC' else (answer['kind'] if has_key else 'sum'),
                         (answer['answer'] if has_key and institution=='UFSC' else None),
                         (answer['np'] if has_key and institution=='UFSC' else None),
                         json.dumps(getattr(q,'references',[]), ensure_ascii=False) if institution=='UFSC' else '[]'))
            return {'edition_id': edition_id, 'imported': len(parsed), 'answers': found_answers,
                    'annulled': annulled_count,'institution':institution, 'warnings': warnings}
        except ValueError as exc:
            for remote_key in r2_keys:
                safe_delete(remote_key)
            raise HTTPException(422, str(exc)) from exc
        except Exception:
            for remote_key in r2_keys:
                safe_delete(remote_key)
            raise


@app.delete('/api/editions/{edition_id}')
def delete_edition(edition_id: int, request: Request):
    with database() as db:
        docs = [dict(r) for r in db.execute('SELECT d.id,d.path FROM documents d JOIN editions e ON e.id=d.edition_id WHERE e.id=%s AND e.user_id=%s', (edition_id,user_id(request)))]
        deleted = db.execute('DELETE FROM editions WHERE id=%s AND user_id=%s', (edition_id,user_id(request))).rowcount
        if not deleted:
            raise HTTPException(404, 'Edição não encontrada')
    for doc in docs:
        safe_delete(doc['path'])
        safe_delete_cache(f'cache/{doc["id"]}/')
    return {'ok': True}


@app.get('/api/questions')
def questions(request: Request, edition_id: int | None = None, subject: str | None = None, search: str = '',
              limit: int = 80, offset: int = 0, institution: str = 'UDESC'):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    institution = get_institution(institution)
    filters, params = ['e.user_id=%s','e.institution=%s'], [user_id(request),institution]
    if edition_id:
        filters.append('q.edition_id=%s'); params.append(edition_id)
    if subject:
        filters.append('q.subject=%s'); params.append(subject)
    if search.strip():
        filters.append('q.text_content LIKE %s'); params.append('%' + search.strip() + '%')
    clause = 'WHERE ' + ' AND '.join(filters) if filters else ''
    with database() as db:
        total = db.execute('SELECT COUNT(*) AS count FROM questions q JOIN editions e ON e.id=q.edition_id ' + clause, params).fetchone()['count']
        rows = db.execute('''SELECT q.*, e.label AS edition,e.institution,e.booklet_color,
                  COALESCE(LENGTH(TRIM(n.body)),0)>0 AS has_note
                  FROM questions q JOIN editions e ON q.edition_id=e.id
                  LEFT JOIN notes n ON n.question_id=q.id ''' + clause +
                  ' ORDER BY e.label DESC, q.session DESC, q.number LIMIT %s OFFSET %s', params + [limit, offset]).fetchall()
    return {'total': total, 'items': [dict(row, **{'annulled': bool(row['annulled']), 'has_note': bool(row['has_note'])}) for row in rows]}


@app.get('/api/questions/{question_id}')
def question_detail(question_id: int, request: Request):
    with database() as db:
        q = get_question(db, question_id, user_id(request))
        note = db.execute('SELECT body FROM notes WHERE question_id=%s', (question_id,)).fetchone()
    return dict(question_payload(q), correct_answer=q['correct_answer'],
                note=note['body'] if note else '')


class QuestionEdit(BaseModel):
    subject: str = Field(min_length=2, max_length=80)
    correct_answer: str | None = None
    annulled: bool = False
    numeric_key: int | None = Field(default=None,ge=0,le=127)
    proposition_count: int | None = Field(default=None,ge=1,le=7)
    difficulty: int | None = Field(default=None, ge=1, le=5)


@app.put('/api/questions/{question_id}')
def edit_question(question_id: int, payload: QuestionEdit, request: Request):
    answer = payload.correct_answer or None
    if answer is not None and answer not in 'ABCDE':
        raise HTTPException(422, 'Resposta precisa ser A, B, C, D ou E')
    with database() as db:
        get_question(db, question_id, user_id(request))
        row=get_question(db,question_id,user_id(request))
        if row['answer_kind']!='choice':
            if row['answer_kind']=='open' and payload.numeric_key is not None and payload.numeric_key>99:
                raise HTTPException(422,'Resposta aberta deve estar entre 00 e 99')
            if payload.numeric_key is not None and row['answer_kind']=='sum' and payload.proposition_count:
                if payload.numeric_key > sum(2**i for i in range(payload.proposition_count)):
                    raise HTTPException(422,'Somatório maior que as proposições disponíveis')
            if row['answer_kind']=='sum' and payload.numeric_key is not None and not payload.proposition_count:
                raise HTTPException(422,'Informe o número de proposições')
            db.execute('''UPDATE questions SET subject=%s,numeric_key=%s,proposition_count=%s,annulled=%s,difficulty=%s WHERE id=%s''',
                (payload.subject.strip(),None if payload.annulled else payload.numeric_key,payload.proposition_count,int(payload.annulled),payload.difficulty,question_id))
        else:
            db.execute('''UPDATE questions SET subject=%s, correct_answer=%s,annulled=%s,difficulty=%s WHERE id=%s''',
                   (payload.subject.strip(), None if payload.annulled else answer,
                    int(payload.annulled), payload.difficulty, question_id))
    return {'ok': True}


class NoteEdit(BaseModel):
    body: str = Field(max_length=30000)


@app.put('/api/questions/{question_id}/note')
def save_note(question_id: int, payload: NoteEdit, request: Request):
    with database() as db:
        get_question(db, question_id, user_id(request))
        db.execute('''INSERT INTO notes(question_id,body,updated_at) VALUES(%s,%s,%s)
                      ON CONFLICT(question_id) DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at''',
                   (question_id, payload.body, utcnow()))
    return {'ok': True}


@app.get('/api/notes')
def notes(request: Request, institution: str = 'UDESC'):
    institution=get_institution(institution)
    with database() as db:
        return [dict(r) for r in db.execute('''SELECT n.question_id,n.body,n.updated_at,q.subject,q.number,
                     q.session,e.label AS edition,e.institution,e.booklet_color FROM notes n
                     JOIN questions q ON q.id=n.question_id JOIN editions e ON e.id=q.edition_id
                     WHERE e.user_id=%s AND e.institution=%s AND TRIM(n.body)<>'' ORDER BY n.updated_at DESC''', (user_id(request),institution))]


def _render_image(document_id: int, pdf_key: str, cache_name: str, page_num: int,
                  scale: float, clip: list[float] | None = None):
    # Immutable cache keys include scale and crop/page coordinates. Private R2 bucket.
    fingerprint = hashlib.sha256(json.dumps([cache_name,page_num,scale,clip]).encode()).hexdigest()[:24]
    key = f'cache/{document_id}/{fingerprint}.png'
    data = get_optional_bytes(key)
    if data is None:
        pdf = get_bytes(pdf_key)
        with fitz.open(stream=pdf, filetype='pdf') as doc:
            if page_num < 0 or page_num >= len(doc):
                raise HTTPException(404, 'Página indisponível')
            pix = doc[page_num].get_pixmap(matrix=fitz.Matrix(scale,scale),
                                          clip=fitz.Rect(clip) if clip else None, alpha=False)
            data = pix.tobytes('png')
        upload_bytes(data,key)
    return Response(content=data, media_type='image/png',
                    headers={'Cache-Control':'private, no-store'})


@app.get('/api/questions/{question_id}/render/{clip_index}')
def question_render(question_id: int, clip_index: int, request: Request, scale: float = 1.7):
    if not (0.7 <= scale <= 2.8):
        raise HTTPException(422,'Escala inválida')
    with database() as db:
        q = get_question(db, question_id, user_id(request))
        clips = json.loads(q['clips_json'])
        if clip_index < 0 or clip_index >= len(clips):
            raise HTTPException(404,'Recorte inexistente')
        key = _document_key(db, q['document_id'], user_id(request))
    clip = clips[clip_index]
    return _render_image(q['document_id'],key,f'clip:{q["id"]}:{clip_index}',
                         clip['page'],scale,clip['rect'])


@app.get('/api/questions/{question_id}/reference/{reference_index}/{clip_index}')
def question_reference_render(question_id: int, reference_index: int, clip_index: int,
                              request: Request, scale: float = 1.7):
    if not (0.7 <= scale <= 2.8):
        raise HTTPException(422, 'Escala inválida')
    with database() as db:
        q = get_question(db, question_id, user_id(request))
        references = json.loads(q.get('reference_clips_json') or '[]')
        if not (0 <= reference_index < len(references)):
            raise HTTPException(404, 'Referência inexistente')
        clips = references[reference_index].get('clips', [])
        if not (0 <= clip_index < len(clips)):
            raise HTTPException(404, 'Recorte de referência inexistente')
        key = _document_key(db, q['document_id'], user_id(request))
    clip = clips[clip_index]
    return _render_image(q['document_id'], key,
                         f'reference:{q["id"]}:{reference_index}:{clip_index}',
                         clip['page'], scale, clip['rect'])


@app.get('/api/questions/{question_id}/page/{page_offset}')
def question_full_page(question_id: int, page_offset: int, request: Request, scale: float = 1.35):
    if not (0.7 <= scale <= 2.3) or abs(page_offset) > 100:
        raise HTTPException(422,'Escala ou deslocamento inválido')
    with database() as db:
        q = get_question(db, question_id, user_id(request))
        key = _document_key(db,q['document_id'], user_id(request))
    return _render_image(q['document_id'],key,f'page:{q["id"]}:{page_offset}',
                         q['source_page']+page_offset,scale)


@app.get('/api/documents/{document_id}/pdf')
def download_original(document_id: int, request: Request):
    with database() as db:
        key = _document_key(db,document_id, user_id(request))
    return Response(get_bytes(key),media_type='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="udesc-{document_id}.pdf"',
                             'Cache-Control':'private, no-store'})


class ExamCreate(BaseModel):
    institution: str = 'UDESC'
    edition_ids: list[int] = Field(default_factory=list, max_length=100)
    subjects: list[str] = Field(default_factory=list, max_length=30)
    count: int = Field(default=20, ge=1, le=500)
    shuffle: bool = True
    exclude_annulled: bool = True
    only_with_key: bool = True
    title: str = Field(default='Simulado personalizado', max_length=100)


@app.post('/api/exams')
def create_exam(config: ExamCreate, request: Request):
    institution=get_institution(config.institution)
    filters = ['e.user_id=%s','e.institution=%s']
    params = [user_id(request),institution]
    if config.edition_ids:
        filters.append('q.edition_id IN (' + ','.join(['%s'] * len(config.edition_ids)) + ')')
        params.extend(config.edition_ids)
    if config.subjects:
        filters.append('q.subject IN (' + ','.join(['%s'] * len(config.subjects)) + ')')
        params.extend(config.subjects)
    if config.exclude_annulled:
        filters.append('q.annulled=0')
    if config.only_with_key:
        filters.append('(q.correct_answer IS NOT NULL OR q.numeric_key IS NOT NULL OR q.annulled=1)')
    clause = 'WHERE ' + ' AND '.join(filters) if filters else ''
    with database() as db:
        found = [dict(r) for r in db.execute('''SELECT q.id, q.correct_answer, q.numeric_key, q.answer_kind,q.proposition_count, q.annulled, q.correct_count,
                 q.subject,e.label AS edition,e.institution,e.booklet_color FROM questions q
                 JOIN editions e ON e.id=q.edition_id ''' + clause, params)]
        if not found:
            raise HTTPException(422, 'Nenhuma questão corresponde aos filtros. Confira gabaritos e matérias.')
        # Selection is ALWAYS weighted toward questions with fewer correct answers.
        # Shuffle breaks ties fairly, but can never move a frequently-correct question
        # ahead of a question with fewer correct answers during selection.
        if config.shuffle:
            random.shuffle(found)
            found.sort(key=lambda r: r['correct_count'])  # stable sort preserves random ties
        else:
            found.sort(key=lambda r: (r['correct_count'], r['edition'], r['subject'], r['id']))
        found = found[:config.count]
        if config.shuffle:
            random.shuffle(found)  # randomize presentation, not the prioritized selection
        attempt_id = db.execute('''INSERT INTO attempts(title,config_json,created_at,user_id,institution)
                                   VALUES(%s,%s,%s,%s,%s) RETURNING id''', (config.title, config.model_dump_json(), utcnow(),user_id(request),institution)).fetchone()['id']
        with db.cursor() as cur:
            cur.executemany('''INSERT INTO attempt_items(attempt_id,question_id,position,
                snapshot_subject,snapshot_edition,snapshot_correct,snapshot_annulled,snapshot_kind,snapshot_numeric,snapshot_np)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', [(attempt_id, q['id'], index, q['subject'],
                (q['edition']+(' · '+q['booklet_color'] if q['institution']=='UFSC' else '')), q['correct_answer'], q['annulled'],q['answer_kind'],q['numeric_key'],q['proposition_count']) for index, q in enumerate(found)])
    return {'id': attempt_id, 'count': len(found)}


def exam_detail_payload(db, attempt_id, owner_id):
    attempt = db.execute('SELECT * FROM attempts WHERE id=%s AND user_id=%s', (attempt_id,owner_id)).fetchone()
    if attempt is None:
        raise HTTPException(404, 'Simulado não encontrado')
    completed = attempt['status'] == 'completed'
    items = [dict(r) for r in db.execute('''SELECT i.*,q.number,q.source_page,q.clips_json,
                   q.text_content,q.document_id,q.correct_count,q.reference_clips_json FROM attempt_items i
                   LEFT JOIN questions q ON q.id=i.question_id
                   WHERE i.attempt_id=%s ORDER BY i.position''', (attempt_id,))]
    output = []
    for item in items:
        points=None
        if completed and not item['snapshot_annulled']:
            if item['snapshot_kind']=='choice' and item['snapshot_correct'] is not None:
                points=float(item['selected_answer']==item['snapshot_correct'])
            elif item['snapshot_numeric'] is not None:
                from ufsc import score_question
                answer=str(item['selected_numeric']) if item['selected_numeric'] is not None else None
                points=float(score_question(answer,str(item['snapshot_numeric']),item['snapshot_kind'],item['snapshot_np']))
        output.append({'points':points, 'id': item['question_id'], 'position': item['position'],
                       'edition': item['snapshot_edition'], 'subject': item['snapshot_subject'],
                       'number': item['number'], 'text': item['text_content'],
                       'correct_count': item['correct_count'] if item['correct_count'] is not None else 0,
                       'clips': len(json.loads(item['clips_json'])) if item['clips_json'] else 0,
                       'references': _reference_metadata(item),
                       'source_page': (item['source_page'] + 1) if item['source_page'] is not None else None,
                       'answer_kind':item['snapshot_kind'], 'proposition_count':item['snapshot_np'],
                       'selected': str(item['selected_numeric']) if item['snapshot_kind']!='choice' and item['selected_numeric'] is not None else item['selected_answer'],
                       'numeric_key':item['snapshot_numeric'] if completed else None, 'flagged': bool(item['flagged']),
                       'annulled': bool(item['snapshot_annulled']) if completed else None,
                       'correct_answer': item['snapshot_correct'] if completed else None})
    return {'id': attempt['id'], 'title': attempt['title'], 'created_at': attempt['created_at'],
            'completed_at': attempt['completed_at'], 'status': attempt['status'],
            'items': output, 'institution':attempt['institution'], 'config': json.loads(attempt['config_json'])}


@app.get('/api/exams/{attempt_id}')
def get_exam(attempt_id: int, request: Request):
    with database() as db:
        return exam_detail_payload(db, attempt_id, user_id(request))


class ExamAnswer(BaseModel):
    answer: str | None = None
    flagged: bool = False


@app.put('/api/exams/{attempt_id}/answers/{position}')
def answer_exam(attempt_id: int, position: int, answer: ExamAnswer, request: Request):
    if answer.answer is not None and len(answer.answer)>3:
        raise HTTPException(422, 'Resposta inválida')
    with database() as db:
        attempt = db.execute('SELECT status FROM attempts WHERE id=%s AND user_id=%s FOR UPDATE', (attempt_id,user_id(request))).fetchone()
        if not attempt:
            raise HTTPException(404, 'Simulado não encontrado')
        if attempt['status'] != 'active':
            raise HTTPException(409, 'Simulado já finalizado')
        item = db.execute('SELECT snapshot_kind,snapshot_np FROM attempt_items WHERE attempt_id=%s AND position=%s',(attempt_id,position)).fetchone()
        if not item: raise HTTPException(404,'Questão não encontrada no simulado')
        selected_choice=None; selected_numeric=None
        if item['snapshot_kind']=='choice':
            if answer.answer is not None and answer.answer not in 'ABCDE':raise HTTPException(422,'Alternativa inválida')
            selected_choice=answer.answer
        elif answer.answer is not None:
            if not answer.answer.isdecimal():raise HTTPException(422,'Resposta numérica inválida')
            selected_numeric=int(answer.answer)
            maximum=99 if item['snapshot_kind']=='open' else (2**(item['snapshot_np'] or 7)-1)
            if not (0<=selected_numeric<=maximum):raise HTTPException(422,'Somatório fora do intervalo permitido')
        changed = db.execute('''UPDATE attempt_items SET selected_answer=%s,selected_numeric=%s,flagged=%s
                              WHERE attempt_id=%s AND position=%s''',
                             (selected_choice,selected_numeric,int(answer.flagged), attempt_id, position)).rowcount
        if not changed:
            raise HTTPException(404, 'Questão não encontrada no simulado')
    return {'ok': True}


# PostgreSQL aggregates scores within the same transaction as attempt completion.
# Ownership comes from the question's edition, never from caller-controlled IDs.
_COUNT_CORRECT = """UPDATE questions AS q
SET correct_count = q.correct_count + totals.hits
FROM (
    SELECT question_id, COUNT(*)::INTEGER AS hits
    FROM attempt_items
    WHERE attempt_id=%s AND question_id IS NOT NULL
      AND snapshot_annulled=0 AND (snapshot_correct IS NOT NULL OR snapshot_numeric IS NOT NULL)
      AND ((snapshot_kind='choice' AND selected_answer=snapshot_correct)
           OR (snapshot_kind IN ('sum','open') AND selected_numeric=snapshot_numeric))
    GROUP BY question_id
) AS totals
WHERE q.id=totals.question_id
  AND EXISTS (SELECT 1 FROM editions e WHERE e.id=q.edition_id AND e.user_id=%s)
"""

_UNCOUNT_CORRECT = """UPDATE questions AS q
SET correct_count = GREATEST(0, q.correct_count - totals.hits)
FROM (
    SELECT question_id, COUNT(*)::INTEGER AS hits
    FROM attempt_items
    WHERE attempt_id=%s AND question_id IS NOT NULL
      AND snapshot_annulled=0 AND (snapshot_correct IS NOT NULL OR snapshot_numeric IS NOT NULL)
      AND ((snapshot_kind='choice' AND selected_answer=snapshot_correct)
           OR (snapshot_kind IN ('sum','open') AND selected_numeric=snapshot_numeric))
    GROUP BY question_id
) AS totals
WHERE q.id=totals.question_id
  AND EXISTS (SELECT 1 FROM editions e WHERE e.id=q.edition_id AND e.user_id=%s)
"""


@app.post('/api/exams/{attempt_id}/finish')
def finish_exam(attempt_id: int, request: Request):
    with database() as db:
        # Lock the attempt row, also locked by answer updates/deletion. This makes
        # finishing idempotent even if two requests arrive at the same time.
        attempt = db.execute('SELECT status,institution FROM attempts WHERE id=%s AND user_id=%s FOR UPDATE',
                             (attempt_id,user_id(request))).fetchone()
        if not attempt:
            raise HTTPException(404, 'Simulado não encontrado')
        if attempt['status'] != 'completed':
            db.execute("UPDATE attempts SET status='completed',completed_at=%s WHERE id=%s AND user_id=%s",
                       (utcnow(), attempt_id,user_id(request)))
            db.execute(_COUNT_CORRECT, (attempt_id,user_id(request)))
        items = [dict(r) for r in db.execute('SELECT * FROM attempt_items WHERE attempt_id=%s', (attempt_id,))]
        report=grade_objective(items,attempt['institution'])
        db.execute('UPDATE attempts SET objective_points=%s,objective_max=%s WHERE id=%s AND user_id=%s',
                   (report['objective_points'],report['objective_max'],attempt_id,user_id(request)))
    report['id']=attempt_id
    report['institution']=attempt['institution']
    report['scoring_note'] = ('Pontuação objetiva COPERVE, com crédito parcial. Em 2019 anuladas não pontuam; em 2024/2025 valem 1,00 para todos. Não é a Nota Final oficial (NF): faltam pesos do curso, língua estrangeira, redação e discursivas.'
        if attempt['institution']=='UFSC' else 'Acertos objetivos UDESC.')
    return report



@app.get('/api/attempts')
def attempts(request: Request, institution: str = 'UDESC'):
    institution=get_institution(institution)
    with database() as db:
        return [dict(r) for r in db.execute('''SELECT a.id,a.title,a.status,a.created_at,a.completed_at,a.institution,a.objective_points,a.objective_max,
           COUNT(i.position) AS total,
           SUM(CASE WHEN i.selected_answer IS NOT NULL THEN 1 ELSE 0 END) AS answered,
           SUM(CASE WHEN (i.snapshot_correct IS NOT NULL OR i.snapshot_numeric IS NOT NULL) AND i.snapshot_annulled=0 THEN 1 ELSE 0 END) AS graded,
           SUM(CASE WHEN ((i.snapshot_kind='choice' AND i.snapshot_correct=i.selected_answer AND i.snapshot_correct IS NOT NULL) OR (i.snapshot_kind IN ('sum','open') AND i.snapshot_numeric=i.selected_numeric AND i.snapshot_numeric IS NOT NULL))
                       AND i.snapshot_annulled=0 THEN 1 ELSE 0 END) AS correct
           FROM attempts a LEFT JOIN attempt_items i ON i.attempt_id=a.id
           WHERE a.user_id=%s AND a.institution=%s GROUP BY a.id ORDER BY a.created_at DESC''', (user_id(request),institution))]


@app.delete('/api/attempts/{attempt_id}')
def delete_attempt(attempt_id: int, request: Request):
    with database() as db:
        attempt = db.execute('SELECT status,institution FROM attempts WHERE id=%s AND user_id=%s FOR UPDATE',
                             (attempt_id,user_id(request))).fetchone()
        if not attempt:
            raise HTTPException(404, 'Simulado não encontrado')
        # Deleting a completed attempt removes its contribution to the counter.
        # Deletion of an edition alone does not change historical snapshots.
        if attempt['status'] == 'completed':
            db.execute(_UNCOUNT_CORRECT, (attempt_id,user_id(request)))
        db.execute('DELETE FROM attempts WHERE id=%s AND user_id=%s', (attempt_id,user_id(request)))
    return {'ok': True}

@app.post('/api/editions/{edition_id}/key')
async def replace_answer_key(edition_id: int, request: Request, key: UploadFile = File(...)):
    """Replace the PDF gabarito in R2; update only matching question answers."""
    with database() as db:
        edition=db.execute('SELECT institution FROM editions WHERE id=%s AND user_id=%s',(edition_id,user_id(request))).fetchone()
        if not edition: raise HTTPException(404,'Edição não encontrada')
        if edition['institution']=='UFSC': raise HTTPException(422,'UFSC utiliza gabaritos separados por dia; reimporte a edição com o gabarito correto')
    remote_key = f'users/{user_id(request)}/editions/{uuid.uuid4().hex}/key.pdf'
    uploaded = False
    with tempfile.TemporaryDirectory(prefix='udesc-key-') as tmp:
        path = Path(tmp) / 'key.pdf'
        try:
            await save_uploaded(key,path)
            mapping = await asyncio.to_thread(parse_key,path)
            if not mapping:
                raise HTTPException(422,'Gabarito não reconhecido; confira o layout do PDF')
            await asyncio.to_thread(upload_file,path,remote_key)
            uploaded = True
            with database() as db:
                old = db.execute('SELECT d.id,d.path FROM documents d JOIN editions e ON e.id=d.edition_id WHERE e.user_id=%s AND d.edition_id=%s AND d.role=%s',
                                 (user_id(request),edition_id,'key')).fetchone()
                if old:
                    db.execute('UPDATE documents SET path=%s,filename=%s WHERE id=%s',
                               (remote_key,key.filename,old['id']))
                else:
                    db.execute('INSERT INTO documents(edition_id,role,path,filename) VALUES(%s,%s,%s,%s)',
                               (edition_id,'key',remote_key,key.filename))
                rows = list(db.execute('SELECT q.id,q.session,q.number FROM questions q JOIN editions e ON e.id=q.edition_id WHERE e.user_id=%s AND q.edition_id=%s',
                                       (user_id(request),edition_id)))
                matched = 0
                for row in rows:
                    k = (row['session'],row['number'])
                    if k in mapping:
                        matched += 1
                        db.execute('UPDATE questions SET correct_answer=%s,annulled=%s WHERE id=%s',
                                   (mapping[k],int(mapping[k] is None),row['id']))
                if not matched:
                    raise HTTPException(422,'Nenhuma resposta corresponde às questões desta edição')
            if old:
                safe_delete(old['path'])
            return {'ok':True,'matched':matched,'total':len(rows)}
        except Exception:
            if uploaded:
                safe_delete(remote_key)
            raise

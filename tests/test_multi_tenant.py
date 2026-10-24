"""Regression tests for registration, persistent login and cross-tenant isolation.

Tests use SQLite for the application SQL adapter. Production remains PostgreSQL.
"""
import os
import sqlite3
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

# A small psycopg shim allows tests to run without a real Neon database.
try:
    import psycopg  # noqa: F401
except ImportError:
    psy = types.ModuleType('psycopg')
    psy.connect = lambda *a, **kw: None
    psyr = types.ModuleType('psycopg.rows')
    psyr.dict_row = object()
    psye = types.ModuleType('psycopg.errors')
    psye.UniqueViolation = sqlite3.IntegrityError
    sys.modules.update({'psycopg': psy, 'psycopg.rows': psyr, 'psycopg.errors': psye})

import pytest
import pymupdf
from fastapi.testclient import TestClient
import app as api
import auth as authentication

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT NOT NULL UNIQUE,password_hash TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE user_sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,csrf_token TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,expires_at TEXT);
CREATE TABLE editions(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER REFERENCES users(id),label TEXT NOT NULL,imported_at TEXT,warning TEXT DEFAULT '',UNIQUE(user_id,label));
CREATE TABLE documents(id INTEGER PRIMARY KEY AUTOINCREMENT,edition_id INTEGER REFERENCES editions(id) ON DELETE CASCADE,role TEXT,path TEXT,filename TEXT);
CREATE TABLE questions(id INTEGER PRIMARY KEY AUTOINCREMENT,edition_id INTEGER REFERENCES editions(id) ON DELETE CASCADE,document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,session TEXT,number INT,subject TEXT,text_content TEXT,clips_json TEXT,source_page INT,correct_answer TEXT,annulled INT DEFAULT 0,difficulty INT, correct_count INT NOT NULL DEFAULT 0 CHECK(correct_count >= 0));
CREATE TABLE notes(question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,body TEXT,updated_at TEXT);
CREATE TABLE attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,title TEXT,config_json TEXT,created_at TEXT,completed_at TEXT,status TEXT DEFAULT 'active');
CREATE TABLE attempt_items(attempt_id INTEGER REFERENCES attempts(id) ON DELETE CASCADE,question_id INTEGER REFERENCES questions(id) ON DELETE SET NULL,position INT,selected_answer TEXT,flagged INT DEFAULT 0,snapshot_subject TEXT,snapshot_edition TEXT,snapshot_correct TEXT,snapshot_annulled INT DEFAULT 0,PRIMARY KEY(attempt_id,position));
'''


class Adapter:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')

    def _sql(self, query):
        return (query.replace('%s', '?').replace('now()', 'CURRENT_TIMESTAMP')
                .replace(' FOR UPDATE', '').replace('::INTEGER', '')
                .replace('GREATEST(', 'max('))

    def execute(self, query, args=()):
        return self.conn.execute(self._sql(query), args)

    def cursor(self):
        return self

    def executemany(self, query, rows):
        return self.conn.executemany(self._sql(query), rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def close(self):
        self.conn.close()

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()


@pytest.fixture
def fixture_app(monkeypatch, tmp_path):
    dbfile = tmp_path / 'db.sqlite3'
    conn = sqlite3.connect(dbfile)
    conn.executescript(SCHEMA)
    conn.close()

    @contextmanager
    def fake_db(*args, **kwargs):
        conn = Adapter(dbfile)
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    monkeypatch.setattr(api, 'UniqueViolation', sqlite3.IntegrityError)
    monkeypatch.setattr(api, 'database', fake_db)
    monkeypatch.setattr(authentication, 'database', fake_db)
    objects = {}
    monkeypatch.setattr(api, 'upload_file', lambda path, key: objects.__setitem__(key, Path(path).read_bytes()))
    monkeypatch.setattr(api, 'upload_bytes', lambda data, key: objects.__setitem__(key, data))
    monkeypatch.setattr(api, 'get_bytes', lambda key: objects[key])
    monkeypatch.setattr(api, 'get_optional_bytes', lambda key: objects.get(key))
    monkeypatch.setattr(api, 'delete_object', lambda key: objects.pop(key, None))
    monkeypatch.setattr(api, 'delete_prefix', lambda prefix: [objects.pop(k) for k in list(objects) if k.startswith(prefix)])
    monkeypatch.setattr(api, 'parse_key', lambda path: {('morning', 1):'A'})
    monkeypatch.setattr(api, 'parse_proof', lambda *a, **kw: [SimpleNamespace(number=1, session='morning',
                         subject='Matemática', text='Enunciado',clips=[{'page':0,'rect':[0,0,300,300]}],source_page=0)])
    doc = tmp_path / 'test.pdf'
    with pymupdf.open() as pdf:
        pdf.new_page(width=595, height=842)
        pdf.save(doc)
    return (lambda: TestClient(api.app, base_url='https://testserver')), doc, objects, dbfile


def register(client, email):
    response = client.post('/api/auth/register', json={'email':email,'password':'correct-horse-battery-staple'}, headers={'origin':'https://testserver'})
    assert response.status_code == 200, response.text
    return response.json()['csrf_token']


def write(client, path, csrf, method='POST', **kwargs):
    return client.request(method,path,headers={'x-csrf-token':csrf,'origin':'https://testserver'},**kwargs)


def import_pdf(client, doc, csrf):
    with doc.open('rb') as a, doc.open('rb') as b:
        return write(client,'/api/editions/import', csrf, data={'label':'2025/1'},
                     files={'morning':('test.pdf',a,'application/pdf'), 'key':('key.pdf',b,'application/pdf')})


def test_auth_and_tenant_isolation(fixture_app):
    newclient, doc, store, dbfile = fixture_app
    alice = newclient()
    bob = newclient()
    assert alice.get('/api/dashboard').status_code == 401
    assert alice.get('/api/editions').status_code == 401
    assert alice.get('/api/health').status_code == 200
    assert alice.get('/').status_code == 200  # Only auth form displayed; no private content.

    csrf_alice = register(alice, 'ALICE@example.com')
    assert alice.get('/api/auth/me').json()['user']['email'] == 'alice@example.com'
    assert alice.get('/api/dashboard').json()['totals']['editions'] == 0
    assert write(alice, '/api/exams', '', json={}).status_code == 403
    assert write(alice, '/api/exams', csrf_alice, json={'subjects':['Matemática']}).status_code == 422

    imported = import_pdf(alice,doc,csrf_alice)
    assert imported.status_code == 200, imported.text
    edition_id = imported.json()['edition_id']
    question_id = alice.get('/api/questions').json()['items'][0]['id']
    assert alice.get('/api/dashboard').json()['totals']['questions'] == 1

    csrf_bob = register(bob,'bob@example.com')
    assert bob.get('/api/questions').json()['total'] == 0
    assert bob.get('/api/editions').json() == []
    assert bob.get('/api/questions/'+str(question_id)).status_code == 404
    assert bob.get('/api/questions/'+str(question_id)+'/render/0').status_code == 404
    with sqlite3.connect(dbfile) as db:
        document_id = db.execute('SELECT id FROM documents WHERE edition_id=?', (edition_id,)).fetchone()[0]
    assert bob.get(f'/api/documents/{document_id}/pdf').status_code == 404
    assert write(bob, f'/api/questions/{question_id}/note',csrf_bob,method='PUT',json={'body':'steal'}).status_code == 404
    assert write(bob, f'/api/editions/{edition_id}', csrf_bob, method='DELETE').status_code == 404
    assert len(alice.get('/api/editions').json()) == 1

    assert write(alice,f'/api/questions/{question_id}/note',csrf_alice,method='PUT',json={'body':'minha nota'}).status_code == 200
    assert bob.get('/api/notes').json() == []
    assert alice.get('/api/notes').json()[0]['body'] == 'minha nota'

    # Same edition label is allowed for each tenant.
    imported_bob = import_pdf(bob,doc,csrf_bob)
    assert imported_bob.status_code == 200, imported_bob.text
    assert imported_bob.json()['edition_id'] != edition_id
    assert bob.get('/api/questions').json()['total'] == 1

    r = write(alice,'/api/exams', csrf_alice, json={'count':1})
    assert r.status_code == 200, r.text
    exam_id = r.json()['id']
    assert bob.get(f'/api/exams/{exam_id}').status_code == 404
    assert write(bob,f'/api/exams/{exam_id}/finish',csrf_bob).status_code == 404
    assert write(bob,f'/api/exams/{exam_id}/answers/0',csrf_bob,method='PUT',json={'answer':'A'}).status_code == 404
    assert write(alice,f'/api/exams/{exam_id}/answers/0',csrf_alice,method='PUT',json={'answer':'A'}).status_code == 200
    assert write(alice,f'/api/exams/{exam_id}/finish',csrf_alice).json()['correct'] == 1
    assert bob.get('/api/attempts').json() == []
    assert len(alice.get('/api/attempts').json()) == 1

    # Persistent cookie works across requests and the server stores only its hash.
    token = alice.cookies.get(authentication.COOKIE_NAME)
    assert token and 'udesc_session' in alice.cookies
    with sqlite3.connect(dbfile) as db:
        raw = db.execute('SELECT token_hash FROM user_sessions WHERE user_id=(SELECT id FROM users WHERE email=?)',('alice@example.com',)).fetchone()[0]
    assert raw == authentication.token_digest(token)
    assert token not in raw
    assert alice.get('/api/auth/me').status_code == 200
    assert write(alice,'/api/auth/logout',csrf_alice).status_code == 200
    assert alice.get('/api/auth/me').status_code == 401
    assert bob.get('/api/auth/me').status_code == 200


def test_registration_validation(fixture_app):
    newclient, *_ = fixture_app
    a = newclient()
    assert a.post('/api/auth/register',json={'email':'invalid','password':'abcdefghijkl'}).status_code == 422
    assert a.post('/api/auth/register',json={'email':'a@example.com','password':'short'}).status_code == 422
    register(a,'a@example.com')
    assert a.post('/api/auth/register',json={'email':'a@example.com','password':'abcdefghijkl'}).status_code == 409
    assert a.post('/api/auth/login',json={'email':'a@example.com','password':'wrong-password'}).status_code == 401
    assert a.post('/api/auth/login',json={'email':'notregistered@example.com','password':'abcdefghijkl'}).status_code == 401

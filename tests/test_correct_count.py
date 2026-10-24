"""Regression tests for per-user correct counts and priority selection."""
import sqlite3

from test_multi_tenant import fixture_app, register, write, import_pdf


def test_counts_finish_only_and_tenant_separation(fixture_app):
    make_client, pdf, store, dbfile = fixture_app
    alice, bob = make_client(), make_client()
    ca = register(alice, 'alice@example.com')
    cb = register(bob, 'bob@example.com')
    assert import_pdf(alice, pdf, ca).status_code == 200
    assert import_pdf(bob, pdf, cb).status_code == 200
    q_alice = alice.get('/api/questions').json()['items'][0]['id']
    q_bob = bob.get('/api/questions').json()['items'][0]['id']
    assert q_alice != q_bob
    assert alice.get(f'/api/questions/{q_alice}').json()['correct_count'] == 0

    exam_id = write(alice, '/api/exams', ca, json={'count': 1}).json()['id']
    assert write(alice, f'/api/exams/{exam_id}/answers/0', ca, method='PUT', json={'answer': 'A'}).status_code == 200
    # Answering is not sufficient: count changes only on finish.
    assert alice.get('/api/questions').json()['items'][0]['correct_count'] == 0
    assert write(alice, f'/api/exams/{exam_id}/finish', ca).json()['correct'] == 1
    assert alice.get('/api/questions').json()['items'][0]['correct_count'] == 1
    assert alice.get(f'/api/questions/{q_alice}').json()['correct_count'] == 1
    assert bob.get(f'/api/questions/{q_bob}').json()['correct_count'] == 0

    # POST finish is also used to reopen results. It must not increment twice.
    assert write(alice, f'/api/exams/{exam_id}/finish', ca).status_code == 200
    assert alice.get(f'/api/questions/{q_alice}').json()['correct_count'] == 1
    assert write(alice, f'/api/exams/{exam_id}/answers/0', ca, method='PUT', json={'answer': 'B'}).status_code == 409

    # Completed incorrect answers don't count.
    wrong = write(alice, '/api/exams', ca, json={'count': 1}).json()['id']
    assert write(alice, f'/api/exams/{wrong}/answers/0', ca, method='PUT', json={'answer': 'B'}).status_code == 200
    assert write(alice, f'/api/exams/{wrong}/finish', ca).json()['correct'] == 0
    assert alice.get(f'/api/questions/{q_alice}').json()['correct_count'] == 1

    # Deleting a completed attempt removes that attempt's contribution.
    assert write(alice, f'/api/attempts/{exam_id}', ca, method='DELETE').status_code == 200
    assert alice.get(f'/api/questions/{q_alice}').json()['correct_count'] == 0
    assert bob.get(f'/api/questions/{q_bob}').json()['correct_count'] == 0


def test_priority_with_shuffle_and_subject_filters(fixture_app):
    make_client, pdf, _, dbfile = fixture_app
    client = make_client()
    csrf = register(client, 'student@example.com')
    imported = import_pdf(client, pdf, csrf)
    assert imported.status_code == 200
    eid = imported.json()['edition_id']
    with sqlite3.connect(dbfile) as conn:
        document_id = conn.execute('SELECT id FROM documents WHERE edition_id=? AND role=?', (eid, 'morning')).fetchone()[0]
        # Add two more eligible questions with the same owner.
        for number, subject in [(2, 'Química'), (3, 'Física')]:
            conn.execute('''INSERT INTO questions(edition_id, document_id,session,number,
                subject,correct_answer,text_content,clips_json,source_page)
                VALUES(?,?,?,?,?,?,?,?,?)''', (eid, document_id, 'morning', number,
                subject,'A',subject,'[]',0))
        conn.commit()

    def finish_one(subject):
        r = write(client, '/api/exams', csrf, json={'count': 1, 'subjects': [subject]})
        assert r.status_code == 200, r.text
        ident = r.json()['id']
        assert write(client, f'/api/exams/{ident}/answers/0', csrf,method='PUT',json={'answer':'A'}).status_code == 200
        assert write(client, f'/api/exams/{ident}/finish',csrf).status_code == 200

    finish_one('Matemática')
    finish_one('Matemática')
    finish_one('Física')

    def counts():
        return {q['subject']: q['correct_count'] for q in client.get('/api/questions').json()['items']}

    assert counts() == {'Matemática':2,'Química':0,'Física':1}
    for shuffled in [True, False]:
        # No matter whether final presentation is shuffled, the two lowest
        # counters must be chosen: Química(0) and Física(1).
        r = write(client, '/api/exams', csrf, json={'count':2, 'shuffle': shuffled})
        assert r.status_code == 200, r.text
        selected = client.get(f'/api/exams/{r.json()["id"]}').json()['items']
        assert {q['subject'] for q in selected} == {'Química','Física'}
        assert sorted(q['correct_count'] for q in selected) == [0,1]
        # Cancel or delete an unfinished attempt: don't modify scores.
        assert write(client,f'/api/attempts/{r.json()["id"]}',csrf,method='DELETE').status_code == 200
    # Explicit subject selection still takes precedence over the priority algorithm.
    r = write(client,'/api/exams',csrf,json={'count':1,'subjects':['Matemática']})
    assert client.get(f'/api/exams/{r.json()["id"]}').json()['items'][0]['subject'] == 'Matemática'


def test_annulled_question_never_counted(fixture_app):
    client_factory, pdf, _, dbfile = fixture_app
    client = client_factory()
    csrf = register(client,'student@example.com')
    assert import_pdf(client,pdf,csrf).status_code == 200
    qid = client.get('/api/questions').json()['items'][0]['id']
    # Attach an annulled question and explicitly include it in the exam.
    with sqlite3.connect(dbfile) as db:
        edition_id, doc_id = db.execute('SELECT edition_id,document_id FROM questions WHERE id=?',(qid,)).fetchone()
        db.execute('''INSERT INTO questions(edition_id,document_id,session,number,subject,
            correct_answer,annulled,text_content,clips_json,source_page)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',(edition_id,doc_id,'morning',2,'Matemática',None,1,'Anulada','[]',0))
        db.commit()
    r=write(client,'/api/exams',csrf,json={'count':2,'exclude_annulled':False,'only_with_key':False})
    assert r.status_code == 200, r.text
    aid=r.json()['id']
    for i in range(2):
        write(client,f'/api/exams/{aid}/answers/{i}',csrf,method='PUT',json={'answer':'A'})
    result=write(client,f'/api/exams/{aid}/finish',csrf).json()
    assert result['correct']==1 and result['annulled']==1
    assert sum(q['correct_count'] for q in client.get('/api/questions').json()['items'])==1

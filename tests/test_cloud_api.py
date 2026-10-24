"""Rendering, answer keys, cache and historical results through authenticated API."""
from test_multi_tenant import fixture_app, register, write, import_pdf


def test_authenticated_question_media_and_history(fixture_app):
    newclient, doc, objects, dbfile = fixture_app
    client = newclient()
    csrf = register(client, 'student@example.com')
    imported = import_pdf(client, doc, csrf)
    assert imported.status_code == 200, imported.text
    edition_id = imported.json()['edition_id']
    q = client.get('/api/questions').json()['items'][0]
    qid = q['id']
    assert write(client,f'/api/questions/{qid}/note', csrf,method='PUT',json={'body':'Revisar sistemas'}).status_code == 200
    image = client.get(f'/api/questions/{qid}/render/0')
    assert image.status_code == 200 and image.content.startswith(b'\x89PNG')
    assert any(k.startswith('cache/') for k in objects)
    image2 = client.get(f'/api/questions/{qid}/render/0')
    assert image.content == image2.content
    assert any(k.startswith('users/') and k.endswith('morning.pdf') for k in objects)

    exam = write(client,'/api/exams', csrf,json={'subjects':['Matemática'],'count':1,'shuffle':False})
    assert exam.status_code == 200, exam.text
    eid = exam.json()['id']
    assert write(client,f'/api/exams/{eid}/answers/0', csrf,method='PUT',json={'answer':'A','flagged':True}).status_code == 200
    assert write(client,f'/api/exams/{eid}/finish',csrf).json()['graded'] == 1
    assert client.get('/api/attempts').json()[0]['status'] == 'completed'
    assert write(client,f'/api/editions/{edition_id}',csrf,method='DELETE').status_code == 200
    assert not any(k.startswith(('users/','cache/')) for k in objects)
    assert client.get('/api/attempts').json()[0]['status'] == 'completed'

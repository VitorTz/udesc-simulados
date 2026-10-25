"""Isolated UFSC PDF context tests; no OCR or external services required."""
import json

from test_multi_tenant import fixture_app
import pymupdf

from ufsc import UFSCQuestion, _shared_sources, _mentioned_sources


def sample_document():
    document=pymupdf.open()
    p=document.new_page(width=595,height=842)
    p.insert_text((55,60),'Texto 1',fontsize=14)
    p.draw_rect(pymupdf.Rect(55,90,340,330),color=(1,0,0)) # picture in source
    p.insert_text((55,370),'01) Com base no texto 1, responda:',fontsize=12)
    p.insert_text((55,590),'02) Com base no texto 1, responda:',fontsize=12)
    p.insert_text((55,740),'Texto 2',fontsize=14)
    p=document.new_page(width=595,height=842)
    p.insert_text((55,70),'03) Com base no texto 2, responda:',fontsize=12)
    p.insert_text((55,350),'Texto 3',fontsize=14)
    p=document.new_page(width=595,height=842)
    p.insert_text((55,70),'04) Com base no texto 3, responda:',fontsize=12)
    return document


def test_prior_text_and_image_is_present_in_each_question():
    with sample_document() as pdf:
        positions=[(1,0,360),(2,0,580),(3,1,60),(4,2,60)]
        questions=[UFSCQuestion('morning',n,'Português','',[],p) for n,p,y in positions]
        _shared_sources(pdf,questions,positions)
        assert [[x['label'] for x in q.references] for q in questions] == [
            ['Texto 1'],['Texto 1'],['Texto 2'],['Texto 3']]
        assert questions[0].references[0]['clips'][0]['page']==0
        assert questions[0].references[0]['clips'][0]['rect'][3]>330  # picture included
        assert questions[1].references[0]==questions[0].references[0]
        assert questions[2].references[0]['clips'][0]['page']==0
        assert questions[3].references[0]['clips'][0]['page']==1


def test_multiple_references_and_ranges():
    assert _mentioned_sources('Com base nos textos 4 e 5, assinale:')==[4,5]
    assert _mentioned_sources('Com base nos textos 1 a 11, assinale:')==list(range(1,12))
    assert _mentioned_sources('Com base no texto 2:')==[2]


def test_source_columns_separately():
    with pymupdf.open() as pdf:
        p=pdf.new_page(width=595,height=842)
        p.insert_text((110,65),'Texto 5',fontsize=12)
        p.insert_text((390,65),'Texto 6',fontsize=12)
        p.draw_rect(pymupdf.Rect(50,95,210,250))
        p.draw_rect(pymupdf.Rect(320,95,510,250))
        p.insert_text((55,390),'06) Com base nos textos 5 e 6:',fontsize=12)
        questions=[UFSCQuestion('morning',6,'Português','',[],0)]
        _shared_sources(pdf,questions,[(6,0,380)])
        refs=questions[0].references
        assert len(refs)==2
        assert refs[0]['clips'][0]['rect'][2]<=refs[1]['clips'][0]['rect'][0]
        assert json.dumps(refs)


def test_authenticated_reference_media_is_tenant_private(fixture_app):
    import sqlite3
    from test_multi_tenant import register, import_pdf
    newclient,doc,objects,dbfile=fixture_app
    first=newclient()
    register(first,'ref-one@example.com')
    # Test fixture uses a single UDESC question, enough to validate the secured
    # media endpoint and metadata shape that UFSC uses as well.
    from test_multi_tenant import write
    csrf=first.get('/api/auth/me').json()['csrf_token']
    edition=import_pdf(first,doc,csrf)
    assert edition.status_code==200,edition.text
    q=first.get('/api/questions').json()['items'][0]
    clip={'page':0,'rect':[0,0,250,250]}
    with sqlite3.connect(dbfile) as db:
        db.execute('UPDATE questions SET reference_clips_json=? WHERE id=?',
                   (json.dumps([{'label':'Texto 1','clips':[clip]}]),q['id']))
    details=first.get(f"/api/questions/{q['id']}").json()
    assert details['references']==[{'label':'Texto 1','clips':1}]
    image=first.get(f"/api/questions/{q['id']}/reference/0/0")
    assert image.status_code==200
    assert image.headers['content-type']=='image/png'
    assert first.get(f"/api/questions/{q['id']}/reference/1/0").status_code==404
    second=newclient()
    register(second,'ref-two@example.com')
    assert second.get(f"/api/questions/{q['id']}/reference/0/0").status_code==404

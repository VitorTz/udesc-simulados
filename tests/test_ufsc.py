"""Official UFSC 2019/2024/2025 booklet and scoring regression tests."""
from pathlib import Path
from decimal import Decimal
import os
import pytest
from ufsc import validate_identity
from ufsc import parse_proof, parse_key, score_question
from ufsc_score import grade
from test_multi_tenant import fixture_app, register, write

ROOT=Path(os.getenv('UFSC_TEST_FIXTURES','/mnt/data'))

def require_fixtures():
    if not (ROOT/'ufsc-p1_azul.pdf').exists():
        pytest.skip('Official UFSC PDFs not provided to test runner')
FILES={
 (2019,'morning'):('2019-p1-amarela.pdf','2019-Gabarito - PROVA 1 - AMARELA.html'),
 (2019,'afternoon'):('2019-p2-amarela.pdf','2019-Gabarito - PROVA 2 - AMARELA.html'),
 (2024,'morning'):('ufsc-2024-p1_azul.pdf','ufsc-2024-novo_gabarito_p1_azul.pdf'),
 (2024,'afternoon'):('ufsc-2024-p2_azul.pdf','ufsc-2024-novo_gabarito_p2_azul.pdf'),
 (2025,'morning'):('ufsc-p1_azul.pdf','ufsc-2025-gabarito_p1_azul.pdf'),
 (2025,'afternoon'):('ufsc-p2_azul.pdf','ufsc-2025-gabarito_p2_azul.pdf'),
}


def test_official_booklets_parse():
 require_fixtures()
 for (year, day),(exam,key) in FILES.items():
  questions=parse_proof(ROOT/exam,day)
  keys=parse_key(ROOT/key,day)
  expected=set(range(1,41)) if day=='afternoon' else set(range(1,13))|set(range(21,41))
  assert len(questions)==len(expected)==len(keys)
  assert {q.number for q in questions}==expected==set(keys)
  assert all(q.clips for q in questions)
  assert all(k['kind'] in ('sum','open') for k in keys.values())
  assert all(k['annulled'] or k['kind']=='open' or k['np'] is not None for k in keys.values())
 assert parse_key(ROOT/'2019-Gabarito - PROVA 2 - AMARELA.html','afternoon')[7]['answer']==17
 assert parse_key(ROOT/'ufsc-2024-novo_gabarito_p1_azul.pdf','morning')[23]['kind']=='open'


def test_scoring_formula():
 # NP=5, correct 02+04+08, selected 02+04 => 0.80
 assert score_question('6','14','sum',5)==Decimal('0.80')
 assert score_question('14','14','sum',5)==Decimal('1.00')
 assert score_question('3','14','sum',5)==Decimal('0.00') # 1 correct +1 wrong
 assert score_question(None,'14','sum',5)==Decimal('0.00')
 assert score_question('24','24','open',None)==Decimal('1.00')
 assert score_question('23','24','open',None)==Decimal('0.00')
 assert score_question('14','14','sum',5,True) is None


def test_aggregate_objective_score():
 items=[{'snapshot_annulled':0,'snapshot_kind':'sum','snapshot_numeric':14,'snapshot_correct':None,'snapshot_np':5,'selected_numeric':6,'selected_answer':None,'snapshot_subject':'Matemática'},
        {'snapshot_annulled':0,'snapshot_kind':'open','snapshot_numeric':24,'snapshot_correct':None,'snapshot_np':None,'selected_numeric':24,'selected_answer':None,'snapshot_subject':'Matemática'},
        {'snapshot_annulled':1,'snapshot_kind':'sum','snapshot_numeric':None,'snapshot_correct':None,'snapshot_np':5,'selected_numeric':None,'selected_answer':None,'snapshot_subject':'Química','snapshot_edition':'2024/1'}]
 result=grade(items,'UFSC')
 assert result['objective_points']==2.80 and result['objective_max']==3
 assert result['correct']==1 and result['annulled']==1


def test_backend_real_import_and_exam(fixture_app):
 require_fixtures()
 factory,_,_,_=fixture_app
 client=factory();csrf=register(client,'ufsc@example.com')
 with (ROOT/'ufsc-2024-p1_azul.pdf').open('rb') as proof, (ROOT/'ufsc-2024-novo_gabarito_p1_azul.pdf').open('rb') as key:
  response=write(client,'/api/editions/import',csrf,data={'institution':'UFSC','label':'2024/1','color':'azul'},
       files={'morning':('p1_azul.pdf',proof,'application/pdf'),'key_morning':('g1_azul.pdf',key,'application/pdf')})
 assert response.status_code==200,response.text
 assert response.json()['imported']==32
 assert len(client.get('/api/editions?institution=UFSC').json())==1
 assert client.get('/api/editions').json()==[]
 assert client.get('/api/questions?institution=UFSC').json()['total']==32
 r=write(client,'/api/exams',csrf,json={'institution':'UFSC','count':1,'subjects':['Matemática']})
 assert r.status_code==200,r.text
 attempt_id=r.json()['id']
 exam=client.get(f'/api/exams/{attempt_id}').json(); item=exam['items'][0]
 assert item['answer_kind'] in ('sum','open')
 if item['answer_kind']=='sum': selected='0'
 else: selected='24'
 x=write(client,f'/api/exams/{attempt_id}/answers/0',csrf,method='PUT',json={'answer':selected})
 assert x.status_code==200,x.text
 finish=write(client,f'/api/exams/{attempt_id}/finish',csrf).json()
 assert finish['institution']=='UFSC' and finish['objective_max']==1
 assert 'não' in finish['scoring_note'].lower()
 assert client.get('/api/attempts').json()==[]
 assert len(client.get('/api/attempts?institution=UFSC').json())==1


def test_annulled_regulation_changed_between_2019_and_2024():
    base={'snapshot_annulled':1,'snapshot_kind':'sum','snapshot_numeric':None,'snapshot_correct':None,'snapshot_np':5,'selected_numeric':None,'selected_answer':None,'snapshot_subject':'Biologia'}
    assert grade([dict(base,snapshot_edition='2019/1')],'UFSC')['objective_points']==0
    assert grade([dict(base,snapshot_edition='2019/1')],'UFSC')['objective_max']==0
    assert grade([dict(base,snapshot_edition='2024/1')],'UFSC')['objective_points']==1
    assert grade([dict(base,snapshot_edition='2024/1')],'UFSC')['objective_max']==1


def test_reject_wrong_booklet_day():
    require_fixtures()
    with pytest.raises(ValueError,match='dia incorreto'):
        validate_identity(ROOT/'ufsc-2025-gabarito_p2_azul.pdf','morning','azul',2025)


def test_two_days_html_and_pdf_answer_keys(fixture_app):
    require_fixtures()
    from contextlib import ExitStack
    factory, _, _, _=fixture_app
    client=factory();csrf=register(client,'two-days@example.com')
    for year,color in [(2019,'amarela'),(2025,'azul')]:
        with ExitStack() as stack:
            docs={}
            for day,param,keyparam in [('morning','morning','key_morning'),('afternoon','afternoon','key_afternoon')]:
                proof,answer=FILES[(year,day)]
                p=stack.enter_context((ROOT/proof).open('rb'))
                a=stack.enter_context((ROOT/answer).open('rb'))
                docs[param]=(proof,p,'application/pdf')
                docs[keyparam]=(answer,a,'text/html' if answer.endswith('.html') else 'application/pdf')
            r=write(client,'/api/editions/import',csrf,data={'institution':'UFSC','label':f'{year}/1','color':color},files=docs)
        assert r.status_code==200,(year,r.text)
        assert r.json()['imported']==72
        assert r.json()['answers']+r.json()['annulled']==72
    assert len(client.get('/api/editions?institution=UFSC').json())==2
    assert client.get('/api/editions').json()==[]

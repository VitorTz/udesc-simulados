"""PDF parser tests that do not depend on NeonDB, Cloudflare or real exams."""
from pathlib import Path
import fitz
from parser import parse_proof, subject_for


def test_subject_ranges_by_year():
    assert subject_for('afternoon', 38, 2025) == 'História'
    assert subject_for('afternoon', 38, 2026) == 'Filosofia'
    assert subject_for('afternoon', 50, 2025) == 'Geografia'
    assert subject_for('afternoon', 50, 2026) == 'Sociologia'
    assert subject_for('morning', 29, 2025) == 'Língua Estrangeira'


def test_parse_searchable_pdf(tmp_path: Path):
    path = tmp_path / 'searchable.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=595,height=842)
        page.insert_text((50,100),'MATEMÁTICA', fontsize=14)
        page.insert_text((50,140),'Questão 01', fontsize=12)
        page.insert_text((50,180),'Calcule 2+2. Alternativas A B C D E', fontsize=11)
        page.insert_text((50,280),'Questão 02', fontsize=12)
        page.insert_text((50,320),'Qual e a soma?', fontsize=11)
        doc.save(path)
    questions = parse_proof(path,'morning',year=2025)
    assert [q.number for q in questions] == [1,2]
    assert all(q.subject == 'Matemática' for q in questions)
    assert all(q.clips for q in questions)

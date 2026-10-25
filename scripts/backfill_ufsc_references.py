"""Rebuild UFSC supporting-text references for already-imported editions.

Example: python -m scripts.backfill_ufsc_references --execute
Requires PostgreSQL migration 005 and the existing .env variables.
Safe for finished attempts, notes, existing image caches and other users.
"""
from __future__ import annotations

import argparse
import json
import logging
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from db import database
from storage import get_bytes
from ufsc import parse_proof


def run(edition_id: int | None, execute: bool) -> tuple[int, int]:
    with database() as db:
        query = '''SELECT d.id AS document_id, d.edition_id, d.role, d.path,
                   e.label, e.booklet_color, e.user_id
                   FROM documents d JOIN editions e ON e.id=d.edition_id
                   WHERE e.institution='UFSC' AND d.role IN ('morning','afternoon')'''
        params: tuple = ()
        if edition_id is not None:
            query += ' AND e.id=%s'
            params = (edition_id,)
        documents = [dict(r) for r in db.execute(query + ' ORDER BY e.id,d.role',params)]

    total_questions = 0
    total_references = 0
    for doc in documents:
        try:
            data = get_bytes(doc['path'])
            with tempfile.TemporaryDirectory(prefix='ufsc-reference-') as temp:
                pdf_path = Path(temp) / 'proof.pdf'
                pdf_path.write_bytes(data)
                parsed = parse_proof(pdf_path, doc['role'])
            by_number = {q.number:q for q in parsed}
            with database() as db:
                rows = [dict(r) for r in db.execute('''SELECT q.id,q.number FROM questions q
                   JOIN editions e ON e.id=q.edition_id
                   WHERE e.institution='UFSC' AND q.edition_id=%s
                     AND q.document_id=%s AND q.session=%s AND e.user_id=%s''',
                     (doc['edition_id'],doc['document_id'],doc['role'],doc['user_id']))]
                for row in rows:
                    question = by_number.get(row['number'])
                    if question is None:
                        raise ValueError(f'Questão {row["number"]} não encontrada no PDF da edição {doc["edition_id"]}')
                    if execute:
                        db.execute('''UPDATE questions SET reference_clips_json=%s,
                            clips_json=%s, text_content=%s
                            WHERE id=%s AND edition_id=%s AND document_id=%s''',
                            (json.dumps(question.references,ensure_ascii=False),
                             json.dumps(question.clips,ensure_ascii=False),question.text,
                             row['id'],doc['edition_id'],doc['document_id']))
                    total_references += len(question.references)
                total_questions += len(rows)
            print(f"{'UPDATED' if execute else 'PREVIEW'} edition={doc['label']} color={doc['booklet_color']} "
                  f"session={doc['role']} questions={len(rows)} references="
                  f"{sum(len(by_number[row['number']].references) for row in rows)}")
        except Exception:
            logging.exception('Failed processing edition=%s session=%s; records left unchanged',
                              doc['edition_id'],doc['role'])
            raise
    return total_questions, total_references


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root/'.env',override=False)
    parser=argparse.ArgumentParser(description='Restore shared UFSC texts and images for saved questions')
    parser.add_argument('--edition-id',type=int,help='Only this edition ID')
    parser.add_argument('--execute',action='store_true',help='Save updates; by default preview only')
    options=parser.parse_args()
    questions,refs=run(options.edition_id,options.execute)
    print(f'Total: {questions} questions; {refs} references; mode={"EXECUTE" if options.execute else "DRY RUN"}')

if __name__=='__main__':
    main()

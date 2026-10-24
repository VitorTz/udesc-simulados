"""Regression tests for 2019/1 and 2024/1 scanned official exam layouts.

If the original PDFs are not present under /mnt/data (normal GitHub checkout), the
integration tests skip without hitting NeonDB or Cloudflare.
"""
from __future__ import annotations

from pathlib import Path
import json

import pymupdf
import pytest

from parser import parse_key, parse_proof, subject_for


DOCUMENTS = {
    2019: {
        "morning": "CADERNO_Matutino__2019_1_CURVAS_15431862810001_7424.pdf",
        "afternoon": "CADERNO_Vespertino___2019_1___Diogo_CURVAS_1543186355453_7424.pdf",
        "key": "GABARITO_OFICIAL__2019_1_15435836838338_7424.pdf",
    },
    2024: {
        "morning": "PROVAS_MATUTINO_2024_1_FINAL_17016470984348_17318.pdf",
        "afternoon": "PROVAS_VESPERTINO_2024_1_FINAL_1701647181605_17318.pdf",
        "key": "GABARITO_OFICAL_2024_1_17024951889471_17318.pdf",
    },
}


@pytest.mark.parametrize("year", [2019, 2024])
@pytest.mark.parametrize("session,count", [("morning", 42), ("afternoon", 50)])
def test_official_scanned_exam_maps(year, session, count):
    path = Path("/mnt/data") / DOCUMENTS[year][session]
    if not path.is_file():
        pytest.skip("Original official scanned PDF not available")
    items = parse_proof(path, session, year=year)
    expected = list(range(1, 51)) if session == "afternoon" else [*range(1, 29), *range(37, 51)]
    assert len(items) == count
    assert [q.number for q in items] == expected
    assert {q.number for q in items if q.subject is None} == set()
    assert any(q.number == 12 for q in items)
    if session == "afternoon":
        assert next(q for q in items if q.number == 30).subject == "História"
        assert next(q for q in items if q.number == 36).subject == "História"
        assert next(q for q in items if q.number == 50).subject == "Geografia"
    with pymupdf.open(path) as document:
        for q in items:
            assert q.clips
            assert q.source_page == q.clips[0]["page"]
            for clip in q.clips:
                assert 0 <= clip["page"] < len(document)
                rect = pymupdf.Rect(clip["rect"])
                assert rect.width > 100 and rect.height > 10
                assert document[clip["page"]].rect.contains(rect)


@pytest.mark.parametrize("year", [2019, 2024])
def test_official_historical_answer_keys(year):
    path = Path("/mnt/data") / DOCUMENTS[year]["key"]
    if not path.is_file():
        pytest.skip("Original official answer key PDF not available")
    answers = parse_key(path)
    assert len(answers) == 92
    assert all(answer in (None, "A", "B", "C", "D", "E") for answer in answers.values())
    if year == 2019:
        assert {("morning", 4), ("morning", 7), ("afternoon", 1), ("afternoon", 9)} == {k for k,v in answers.items() if v is None}
        assert answers["afternoon", 42] == "E"  # corrected official answer, not preliminary D
    else:
        assert {( "morning", 39), ("afternoon", 12)} == {k for k,v in answers.items() if v is None}


def test_historical_subject_ranges():
    for year in [2019, 2024, 2025]:
        assert subject_for("afternoon", 30, year) == "História"
        assert subject_for("afternoon", 38, year) == "História"
        assert subject_for("afternoon", 39, year) == "História"
        assert subject_for("afternoon", 50, year) == "Geografia"
    assert subject_for("afternoon", 38, 2026) == "Filosofia"
    assert subject_for("afternoon", 50, 2026) == "Sociologia"

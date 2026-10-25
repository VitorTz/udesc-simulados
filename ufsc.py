"""UFSC objective exam parsing and COPERVE scoring (proposições múltiplas)."""
from __future__ import annotations

import html
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pymupdf

WEIGHTS = (1, 2, 4, 8, 16, 32, 64)
QUESTION_RE = re.compile(r'^\s*(?:QUEST[ÃA]O\s+([0-3]?\d|40)\b|([0-3]?\d|40)\s*\))', re.I)


@dataclass
class UFSCQuestion:
    session: str
    number: int
    subject: str
    text: str
    clips: list[dict]
    source_page: int
    references: list[dict] = field(default_factory=list)


def subject_for(day: str, n: int) -> str:
    if day == 'morning':
        if n <= 12: return 'Português e Literatura'
        if n <= 20: return 'Língua Estrangeira'
        if n <= 30: return 'Matemática'
        return 'Biologia'
    if n <= 20: return 'Ciências Humanas e Sociais'
    if n <= 30: return 'Física'
    return 'Química'


def _headings(page: pymupdf.Page):
    found = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            s = ''.join(span['text'] for span in line['spans']).strip()
            m = QUESTION_RE.match(s)
            if not m: continue
            n = int(m.group(1) or m.group(2))
            if not 1 <= n <= 40 or line['bbox'][0] > 110: continue
            found.append((n, max(0, line['bbox'][1] - 3), line['bbox'][0], s.upper().startswith("QUEST")))
    return found



# A UFSC often places source material between two questions or on an earlier page.
# Sources are indexed separately, rather than appended to the preceding question.
SOURCE_HEADING_RE = re.compile(r'^\s*(?:TEXTO|TEXT)\s+(\d{1,2})\s*$', re.I)
SOURCE_MENTION_RE = re.compile(r'\btexto[s]?\s+(\d{1,2})(?:\s*(?:,|e|ou|/)\s*\d{1,2})*(?:\s+a\s+\d{1,2})?', re.I)


def _source_headings(page: pymupdf.Page) -> list[tuple[int, float, float]]:
    """Standalone caption and its position; preserve x for side-by-side sources."""
    hits = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            text = ''.join(span['text'] for span in line['spans']).strip()
            match = SOURCE_HEADING_RE.fullmatch(text)
            if match:
                hits.append((int(match.group(1)), max(0, line['bbox'][1]-5), line['bbox'][0]))
    return hits


def _mentioned_sources(text: str) -> list[int]:
    """Parse references including 'textos 4 e 5', 'textos 3, 4 e 5'."""
    results = []
    for match in SOURCE_MENTION_RE.finditer(text):
        value = match.group()
        nums = [int(n) for n in re.findall(r'\d{1,2}', value)]
        if ' a ' in value.lower() and len(nums) == 2 and 0 < nums[1]-nums[0] <= 12:
            nums = list(range(nums[0], nums[1]+1))
        for number in nums:
            if number not in results:
                results.append(number)
    return results


def _between_pages(pdf: pymupdf.Document, first: tuple[int,float], end: tuple[int,float]) -> list[dict]:
    """Represent a visually complete source, including tables/figures, in PDF points."""
    clips = []
    for i in range(first[0], end[0]+1):
        rect = pdf[i].rect
        top = max(0, first[1]-4) if i == first[0] else 0
        bottom = min(rect.y1, end[1]-5) if i == end[0] else rect.y1
        # Avoid tiny header-only fragments on the page containing the next question.
        if bottom-top < 40:
            continue
        clips.append({'page': i, 'rect': [rect.x0, top, rect.x1, bottom]})
    return clips


def _shared_sources(pdf: pymupdf.Document, questions: list[UFSCQuestion],
                    positions: list[tuple[int,int,float]]) -> None:
    """Locate standalone source sections, attach them to referencing questions.

    Separate sources can share a page, including two texts side by side.
    The shared source is self-contained even in a one-question mixed simulado.
    """
    events = [(page, y, 0.0, 'question', n) for n,page,y in positions]
    for page_number,page in enumerate(pdf):
        for number,y,x in _source_headings(page):
            events.append((page_number,y,x,'source',number))
    events.sort(key=lambda item:(item[0],item[1],item[2]))
    regions = defaultdict(list)
    for page,y,x,kind,number in events:
        if kind != 'source':
            continue
        later = [e for e in events if (e[0],e[1]) > (page,y+3)]
        following = later[0] if later else (len(pdf)-1,pdf[-1].rect.y1,0,'end',0)
        region = _between_pages(pdf,(page,y),(following[0],following[1]))
        if not region:
            continue
        # The 2024 exam places Textos 5 and 6 in two columns at identical y.
        siblings = sorted([e for e in events if e[3]=='source' and e[0]==page and abs(e[1]-y)<=3],
                          key=lambda item:item[2])
        if len(siblings)>1:
            index = next(i for i,e in enumerate(siblings) if e[4]==number and e[2]==x)
            left = (siblings[index-1][2]+x)/2 if index else 0
            right = (x+siblings[index+1][2])/2 if index+1<len(siblings) else pdf[page].rect.x1
            for clip in region:
                if clip['page']==page:
                    clip['rect'][0] = left
                    clip['rect'][2] = right
        regions[number].append(((page,y),region))
    for q in questions:
        page = pdf[q.source_page]
        y = next((y for n,p,y in positions if n==q.number and p==q.source_page),0)
        next_event = next((e for e in events if (e[0],e[1])>(q.source_page,y+3)),
                          (q.source_page,page.rect.y1,0,'end',0))
        text_end = min(next_event[1],y+145) if next_event[0]==q.source_page else min(page.rect.y1,y+145)
        intro = page.get_textbox(pymupdf.Rect(0,y,page.rect.x1,text_end))
        for number in _mentioned_sources(intro):
            candidates = [(pos,clips) for pos,clips in regions.get(number, []) if pos<(q.source_page,y)]
            if candidates:
                _,clips = max(candidates, key=lambda item:item[0])
                q.references.append({'label':f'Texto {number}','clips':clips})
        # Avoid duplicating the introductory source as part of question 01.
        if q.number==1 and q.references:
            q.clips = [clip for clip in q.clips if clip['page']>=q.source_page]
        # Stop the question display before a source intended for a later item.
        next_question = next(((p,py) for n,p,py in positions if n==q.number+1), (len(pdf),0))
        stop = next((e for e in events if e[3]=='source' and
                     (q.source_page,y+3)<(e[0],e[1])<next_question), None)
        if stop:
            cutoff = (stop[0],stop[1])
            clean=[]
            for clip in q.clips:
                if clip['page']>cutoff[0]:
                    continue
                item={'page':clip['page'],'rect':list(clip['rect'])}
                if clip['page']==cutoff[0]:
                    item['rect'][3] = min(item['rect'][3],cutoff[1]-4)
                if item['rect'][3]-item['rect'][1]>=10:
                    clean.append(item)
            q.clips=clean
        # The searchable question text must not absorb the next shared source.
        q.text = '\n'.join(pdf[clip['page']].get_textbox(pymupdf.Rect(clip['rect']))
                           for clip in q.clips)[:15000]

def parse_proof(path: str | Path, day: str) -> list[UFSCQuestion]:
    """Preserve original PDF pages so shared texts, figures and equations are not lost."""
    if day not in ('morning', 'afternoon'):
        raise ValueError('Dia da prova inválido')
    with pymupdf.open(path) as pdf:
        matches = []
        for page_no, page in enumerate(pdf):
            matches.extend((n, page_no, y, x, explicit) for n, y, x, explicit in _headings(page))
        # Filter to strictly increasing complete sequence; malformed headings fail closed.
        by_num = defaultdict(list)
        for number, page, y, x, explicit in matches:
            by_num[number].append((page, y, x, explicit))
        missing = sorted(set(range(1, 41)) - set(by_num))
        duplicated = []
        if missing or duplicated:
            raise ValueError(f'UFSC: numeração inválida no PDF {day}: faltando {missing}, duplicadas {duplicated}')
        matches = sorted([(n, *sorted(by_num[n], key=lambda v:(not v[3],v[2],v[0]))[0][:2]) for n in range(1,41)], key=lambda x:(x[1],x[2]))
        if [n for n, *_ in matches] != list(range(1, 41)):
            raise ValueError('Questões fora de ordem: confira a cor/modelo da prova')
        questions=[]
        for idx,(number, page_no,y) in enumerate(matches):
            nextp = matches[idx+1][1] if idx+1 < len(matches) else len(pdf)-1
            nexty = matches[idx+1][2] if idx+1 < len(matches) else None
            clips=[]
            # Language, humanities and literature often reference source material above or
            # before the heading; display full pages rather than truncating the source text.
            if idx == 0 and page_no > 0:
                for p in range(max(0,page_no-2),page_no):
                    rect=pdf[p].rect
                    clips.append({'page':p,'rect':[rect.x0,rect.y0,rect.x1,rect.y1]})
            for p in range(page_no,nextp+1):
                rect=pdf[p].rect
                end = nexty - 3 if p == nextp and nexty is not None else rect.y1
                start= max(0,y-16) if p==page_no else 0
                if end <= start+8: continue
                # Full-width to preserve illustrations and tables.
                clips.append({'page':p,'rect':[rect.x0,start,rect.x1,min(end,rect.y1)]})
            # For all language/text-based questions allow accessing the whole original
            # page (and previous pages) from the site's page navigator as well.
            text = ' '.join(pdf[p].get_text(sort=True) for p in range(page_no,nextp+1))[:15000]
            questions.append(UFSCQuestion(day,number,subject_for(day,number),text,clips,page_no))
        _shared_sources(pdf, questions, matches)
        return [q for q in questions if q.subject != 'Língua Estrangeira']


def _key_pdf(path: Path):
    records = {}
    with pymupdf.open(path) as pdf:
        if len(pdf) != 1:
            raise ValueError('Gabarito UFSC esperado em PDF de uma página')
        page = pdf[0]
        rows = defaultdict(list)
        for w in page.get_text('words'):
            y = round((w[1]+w[3])/2 / 15.15)  # rows at ~15.15 pt spacing
            rows[y].append((w[0],w[4].strip()))
        for items in rows.values():
            nums=[(x,int(t)) for x,t in items if re.fullmatch(r'\d{1,2}',t) and x<115 and 1<=int(t)<=40]
            if not nums: continue
            n=nums[0][1]
            answers=[t for x,t in items if x>495 and (re.fullmatch(r'\d{1,3}',t) or t.upper() in ('ANULADA','ANULADO'))]
            if not answers: continue
            kind='open' if any(t.upper()=='ABERTA' for x,t in items if 185<x<315) else 'sum'
            ann = answers[-1].upper().startswith('ANUL')
            number=None if ann else int(answers[-1])
            if not ann and kind=='sum' and (number<1 or number>127):
                raise ValueError(f'Somatório inválido na questão {n}: {number}')
            np = next((int(t) for x,t in items if 200<x<335 and re.fullmatch(r'[4-7]',t)), None)
            records[n]={'kind':kind,'answer':number,'annulled':ann,'np':np}
    return records


def _key_html(path: Path):
    from bs4 import BeautifulSoup
    raw=path.read_bytes().decode('cp1252',errors='replace')
    soup=BeautifulSoup(raw,'html.parser')
    records={}
    for tr in soup.find_all('tr'):
        cells=[c.get_text(' ',strip=True).replace('\xa0',' ').strip() for c in tr.find_all(['th','td'],recursive=False)]
        if len(cells)<10 or not re.fullmatch(r'\d{1,2}',cells[0]):continue
        n=int(cells[0]);np_s=cells[2]; val=cells[-1]
        if not 1<=n<=40:continue
        annulled = 'anulad' in val.lower()
        matches = re.findall(r'\d{1,3}',val)
        if not annulled and not matches:continue
        np=int(np_s) if np_s.isdecimal() else None
        kind='open' if 'aberta' in np_s.lower() else 'sum'
        records[n]={'kind':kind,'answer':None if annulled else int(matches[-1]), 'annulled':annulled,'np':np}
    return records


def parse_key(path: str | Path, day: str) -> dict[int, dict]:
    path=Path(path)
    records = _key_html(path) if path.suffix.lower() in ('.html','.htm') else _key_pdf(path)
    expected=set(range(1,41))
    if set(records)!=expected:
        raise ValueError(f'Gabarito UFSC {day}: faltando {sorted(expected-set(records))}')
    # Guard against uploading day 1 as day 2 or an incorrect colored gabarito.
    return {n:row for n,row in records.items() if subject_for(day,n)!='Língua Estrangeira'}


def score_question(selected: str | None, correct: str | None, kind: str, proposition_count: int | None, annulled: bool=False) -> Decimal | None:
    """COPERVE: NPC>NPI => (NP-[NTPC-(NPC-NPI)])/NP, else 0; two decimals."""
    if annulled or correct is None:
        return None
    if selected is None or selected=='': return Decimal('0.00')
    if kind=='open': return Decimal('1.00') if int(selected)==int(correct) else Decimal('0.00')
    n=int(proposition_count or 0)
    if not 1<=n<=7: raise ValueError('Número de proposições da UFSC ausente/inválido')
    valid=sum(WEIGHTS[:n])
    a=int(selected); k=int(correct)
    if a <0 or a>valid or k<0 or k>valid: raise ValueError('Somatório com proposição inexistente')
    npc=(a&k).bit_count()
    npi=(a&~k).bit_count()
    ntpc=k.bit_count()
    if npc <= npi: return Decimal('0.00')
    pts=Decimal(n - (ntpc - (npc-npi))) / Decimal(n)
    return pts.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)


def validate_identity(path: str | Path, day: str, color: str, year: int | None = None):
    """Reject known mismatches between UFSC proof/day/color/year and its official answer key."""
    path=Path(path)
    if path.suffix.lower() in ('.html','.htm'):
        from bs4 import BeautifulSoup
        sample=BeautifulSoup(path.read_bytes().decode('cp1252',errors='replace'),'html.parser').get_text(' ',strip=True)[:1000]
    else:
        with pymupdf.open(path) as pdf:
            sample=' '.join(pdf[i].get_text(sort=True) for i in dict.fromkeys([0,min(1,len(pdf)-1),len(pdf)-1]))
    sample = sample.upper().replace('‒','-').replace('–','-').replace('—','-')
    expected_day='1' if day=='morning' else '2'
    explicit_day=re.search(r'\bPROVA\s*([12])\b',sample)
    if explicit_day and explicit_day.group(1)!=expected_day:
        raise ValueError(f'Gabarito/prova do dia incorreto: esperado Prova {expected_day}, encontrado Prova {explicit_day.group(1)}')
    # Exam booklets have color in their header/footer (often not in filenames).
    colors=('AZUL','AMARELA','VERDE','VIOLETA','CINZA','LARANJA','MARFIM','ROSA','MARROM')
    found_colors=set(re.findall(r'\b(?:'+'|'.join(colors)+r')\b',sample))
    chosen=color.upper()
    if found_colors and chosen not in found_colors:
        raise ValueError(f'Cor incorreta: esperada {color}, documento identifica {sorted(found_colors)}')
    # If the document explicitly labels the exam year, verify it too.
    match=re.search(r'\bVESTIBULAR\s+(?:UNIFICADO\s+)?(?:UFSC/IFSC/IFC[-/]?)?\s*(20\d{2})\b',sample)
    if year and match and int(match.group(1))!=year:
        raise ValueError(f'Gabarito/prova de outro ano ({match.group(1)} em vez de {year})')

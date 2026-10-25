"""UFSC COPERVE per-question objective scoring and safe result aggregation."""
from __future__ import annotations
from decimal import Decimal
import re
from ufsc import score_question


def grade(items, institution="UDESC"):
    points=Decimal('0.00'); maximum=Decimal('0.00')
    exact=0; wrong=0; blank=0; annulled=0; no_key=0; subjects={}
    for i in items:
        if i['snapshot_annulled']:
            annulled+=1
            year_match=re.search(r'20\d{2}',str(i.get('snapshot_edition','')))
            year=int(year_match.group()) if year_match else None
            # 2019: anulações excluídas. 2024/2025: um ponto para todos.
            if institution=='UFSC' and year is not None and year>=2024:
                points+=Decimal('1.00');maximum+=Decimal('1.00')
                st=subjects.setdefault(i['snapshot_subject'],{'total':0,'correct':0,'points':Decimal('0.00')})
                st['total']+=1;st['points']+=Decimal('1.00')
            continue
        kind=i.get('snapshot_kind') or 'choice'
        selected=i.get('selected_numeric') if kind!='choice' else i.get('selected_answer')
        correct=i.get('snapshot_numeric') if kind!='choice' else i.get('snapshot_correct')
        if correct is None:
            no_key+=1;continue
        if kind=='choice':
            score=Decimal('1.00') if selected==correct else Decimal('0.00')
        else:
            score=score_question(str(selected) if selected is not None else None,
                                 str(correct),kind,i.get('snapshot_np'))
        points+=score
        maximum+=1
        st=subjects.setdefault(i['snapshot_subject'], {'total':0,'correct':0,'points':Decimal('0.00')})
        st['total']+=1
        st['points']+=score
        if selected is None: blank+=1
        elif selected==correct:exact+=1;st['correct']+=1
        else: wrong+=1
    for entry in subjects.values(): entry['points']=float(entry['points'])
    return dict(total=len(items),graded=int(maximum),correct=exact,wrong=wrong,unanswered=blank,
                annulled=annulled,without_key=no_key,percentage=round(100*float(points/maximum),2) if maximum else None,
                objective_points=float(points),objective_max=float(maximum),subjects=subjects)

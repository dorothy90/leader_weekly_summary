"""Bounded, exact source spans; see CITATION_MIGRATION.md for design references."""


def citation_candidates(text, max_chars=1800, overlap=150):
    if not 0 <= overlap < max_chars:
        raise ValueError('인용 후보 크기보다 작은 overlap이 필요합니다.')
    candidates=[]
    start=0
    while start<len(text):
        end=min(start+max_chars,len(text))
        if end<len(text):
            # Prefer paragraph, line, sentence, then word boundaries. Leave short tails intact.
            for separator in ('\n\n','\n','. ','。','! ','? ',' '):
                boundary=text.rfind(separator,start+max_chars//2,end)
                if boundary>=0:
                    end=boundary+len(separator)
                    break
        quote=text[start:end]
        if quote.strip():
            candidates.append(dict(candidate_id=f'c{start}_{end}',start=start,end=end,text=quote))
        if end==len(text):
            break
        start=max(start+1,end-overlap)
    return candidates

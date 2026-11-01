"""Conservative repair of small OCR differences using known story text."""
import re
from difflib import SequenceMatcher


def canonical_text(text, known, threshold=.98):
    compact = lambda value: re.sub(r'\W+', '', value.casefold())
    source = compact(text)
    if len(source)<65: return text
    numbers = re.findall(r'\d+(?:[.,]\d+)?', text)
    protected = lambda value: sorted(re.findall(r'\b(?:no|not|never|without|cannot|can\x27t|must|may|before|after|east|west|north|south|left|right|above|below|one|two|three|four|five|six|seven|eight|nine|ten)\b', value.casefold()))
    best,best_score=text,threshold
    for candidate in sorted(known):
        target = compact(candidate)
        if abs(len(source)-len(target)) > max(2,len(source)*(1-threshold)): continue
        if numbers != re.findall(r'\d+(?:[.,]\d+)?',candidate): continue
        if protected(text) != protected(candidate): continue
        if source==target: return candidate
        comparison=SequenceMatcher(None,source,target,autojunk=False)
        if comparison.quick_ratio()<best_score or comparison.ratio()<best_score: continue
        # A whole replaced word is a new instruction, not a minor OCR glitch.
        if any(max(j-i,l-k)>2 for tag,i,j,k,l in comparison.get_opcodes() if tag!='equal'): continue
        best,best_score=candidate,comparison.ratio()
    return best

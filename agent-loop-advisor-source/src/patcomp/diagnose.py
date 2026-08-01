"""Stage 4 — diagnosis against the §2 selection matrix.

The deterministic evidence prior runs BEFORE any model sees the document. It is
a hint, never a verdict. Where a model diagnoser is configured its label is
recorded alongside the prior and disagreement is surfaced; where the interview
answers, the human wins outright.

Multi-label by design: real projects match three or four rows of the matrix.
"""
from __future__ import annotations

import re
from typing import Protocol

from .catalogue import Catalogue
from .models import IR, SignatureLabel

# A term matches when it appears as a phrase, allowing simple inflection at the
# end of a word ("personalis" -> "personalised") and flexible whitespace.
_WORD = re.compile(r"[a-z0-9]+")


def stem(word: str) -> str:
    """Light suffix stripping so 'dependencies' matches 'dependency' and
    'ordered' matches 'order'. Requirements prose inflects; the §2 evidence
    terms are verbatim from the paper, so without this the prior misses
    matches a human would call obvious."""
    w = word
    for suf, repl in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)] + repl
    return w


def normalise(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def stem_text(text: str) -> str:
    return " ".join(stem(w) for w in _WORD.findall(text.lower()))


# Terms too generic to carry signal on their own.
_STOP = {"the", "a", "an", "of", "to", "is", "are", "in", "on", "for", "and",
         "or", "no", "not", "it", "be", "at", "by", "with", "that", "this"}


def term_weight(text_norm: str, term: str, text_stem: str | None = None) -> float:
    """1.0 for an exact phrase, 0.9 stemmed, 0.6 when every content word is present.

    Requirements documents paraphrase; the §2 evidence terms are verbatim from
    the paper. Exact-phrase-only matching makes the prior much weaker than the
    matrix it encodes, so partial credit is given when all the content words of
    a term appear, even if not adjacent.

    `text_stem` is the pre-stemmed document. Pass it to avoid re-stemming the
    whole document on every term (a diagnosis scores ~110 terms); when omitted
    it is computed once here for callers that score a single short span.
    """
    t = normalise(term)
    if not t:
        return 0.0
    padded = f" {text_norm} "
    if f" {t} " in padded:
        return 1.0
    if text_stem is None:
        text_stem = stem_text(text_norm)
    padded_s = f" {text_stem} "
    ts = " ".join(stem(w) for w in t.split())
    if f" {ts} " in padded_s:
        return 0.9
    words = [stem(w) for w in t.split() if w not in _STOP]
    if len(words) >= 2 and all(f" {w} " in padded_s for w in words):
        return 0.6
    return 0.0


def term_hits(text_norm: str, term: str) -> bool:
    return term_weight(text_norm, term) > 0


class ModelDiagnoser(Protocol):
    """Optional LLM stage. The compiler runs fully without one."""

    def label(self, text: str, signature_id: str, problem: str) -> bool: ...


# Negation cues. "It does not compare options, plan, or take actions" is
# evidence AGAINST planning, not for it. Without this the prior reads a
# document's own disclaimers as support and over-sells orchestration — the
# dominant failure mode this compiler exists to avoid.
#
# This is matched against the NORMALISED segment (see negated_spans below),
# so "doesn t"/"don t"/etc. are written the way normalise() actually spells
# a contraction — apostrophes aren't alphanumeric, so _WORD tokenisation
# splits "doesn't" into "doesn" + "t", never leaving the apostrophe in.
# Round 5 (2026-08-01) found that this had been matched against the RAW,
# un-normalised segment since the fix was written: "doesn t" as a literal
# string never appears in real text (which has an apostrophe, "doesn't"),
# so EVERY contraction-based negation — "shouldn't", "doesn't", "can't",
# all of them, not just the two spelled out here — was silently invisible
# to the scanner. "are pure lookups against our policy system and
# shouldn't involve any judgment at all, human or otherwise" let
# "human approval on" fire as if the sentence never said "shouldn't" at
# all. Widened the contraction list now that matching actually works.
_NEG = re.compile(
    r"\b(does not|do not|doesn t|don t|didn t|isn t|aren t|wasn t|weren t|"
    r"haven t|hasn t|hadn t|won t|wouldn t|shouldn t|couldn t|can t|"
    r"mustn t|shan t|no |not |never|without|"
    r"rather than|instead of|nothing that)\b", re.I)
# A negation's scope ends at a coordinating boundary that starts a new
# predicate (";", ":", ", and", ", while"), not at the end of the sentence.
# Without this, "a payment step with no LLM, and exceptions route to human
# review" lets "no LLM" erase the human-review signal three clauses later —
# and without the colon, "the deliverable is a pipeline, not guidance: generate
# migration changes, run validation tests..." lets an unrelated "not guidance"
# erase every evidence term in the list that follows the colon. An em/en-dash
# is the same story one level up: "...80 milliseconds to respond per auction
# ... — cost and speed are the whole ballgame here, not accuracy" is two
# independent clauses joined by a dash, not one; without splitting on it, the
# second clause's "not accuracy" reaches back across the dash and erases
# "milliseconds" in the first. ", not X" at the end of a sentence is the same
# shape again, one level down: "pick the best one with a reason, not run
# endless split tests" and "a considered call..., not a coin flip" are both a
# main clause plus a trailing contrastive aside, not a single clause under one
# negation — without splitting there, the aside's "not" reaches back and
# erases the main clause's own positive evidence. This does not touch the
# list case ("does not compare options, plan, or take actions"): there the
# comma-separated items never have "not" as the word right after the comma.
_SEGMENT = re.compile(r";|:|—|–|,\s+(?:and|while|whereas|not)\b|(?<=[.!?])\s+")


def negated_spans(text: str) -> list[str]:
    """Segments under the scope of a negation cue.

    Within a segment a negation carries across a list ("does not compare
    options, plan, or take actions" negates all three), but it does not cross
    into an independent clause.

    Scope runs FORWARD from the cue to the end of the segment, not across the
    whole segment. "During a network outage... needs to check live service
    status... rather than working off a static script" has "rather than"
    negating the static-script alternative, not the live-status-checking
    that's stated before it — a whole-segment scope wrongly erased the
    earlier, unrelated positive evidence. The "does not compare options,
    plan, or take actions" list case is unaffected: "does not" sits at the
    start of its segment, so the forward scope still covers the whole list.

    Matches against the NORMALISED segment, not the raw one — see _NEG's
    comment for why: normalise() is what actually turns "doesn't" into
    "doesn t", so searching raw text for that literal string never matched
    any real contraction.
    """
    out = []
    for segment in _SEGMENT.split(text):
        if not segment:
            continue
        norm_segment = normalise(segment)
        m = _NEG.search(norm_segment)
        if m:
            out.append(norm_segment[m.start():])
    return out


def score_signatures(
    text: str,
    cat: Catalogue,
    threshold: float = 0.15,
) -> list[SignatureLabel]:
    """Deterministic evidence prior over the §2 matrix."""
    norm = normalise(text)
    norm_stem = stem_text(norm)                      # stem the document ONCE
    neg = [(span, stem_text(span)) for span in negated_spans(text)]
    labels: list[SignatureLabel] = []
    for sig in cat.signatures:
        weights = {t: term_weight(norm, t, norm_stem) for t in sig.evidence}
        # Discount any term whose only support sits inside a negated clause —
        # UNLESS the term itself is what triggered the negation cue. Several
        # evidence terms are themselves absence-shaped ("no exceptions", "no
        # one filing shows", "never bend"): the catalogue's own patterns
        # define these signatures by that absence (04's beats_baseline_when
        # is "not usually. A tendency is not a guarantee"; 10's is "no single
        # document contains it"). Without this exception the term's own "no "
        # makes negated_spans() flag its segment, and the discount then
        # zeroes the very term that caused the flag — the term cancels
        # itself out on every occurrence, not just when something else in
        # the sentence actually negates it.
        for t, w in list(weights.items()):
            if w == 0 or _NEG.search(normalise(t)):
                continue
            if any(term_weight(span, t, span_stem) > 0 for span, span_stem in neg):
                weights[t] = 0.0
        matched = [t for t, w in weights.items() if w > 0]
        # Saturating: two solid term hits is meaningful support, and more hits
        # should not let one verbose signature dominate the ranking.
        raw = sum(weights.values())
        score = min(1.0, raw / 2.0) if raw else 0.0
        labels.append(SignatureLabel(
            signature_id=sig.id,
            problem=sig.problem,
            pattern=sig.pattern,
            advisory=sig.advisory,
            prior_score=score,
            prior_label=score >= threshold,
            matched_terms=matched,
        ))
    return labels


def diagnose(
    ir: IR,
    cat: Catalogue,
    model: ModelDiagnoser | None = None,
    threshold: float = 0.15,
) -> IR:
    ir.signatures = score_signatures(ir.raw_text, cat, threshold)
    if model is not None:
        for lab in ir.signatures:
            try:
                lab.model_label = model.label(ir.raw_text, lab.signature_id, lab.problem)
            except Exception:
                lab.model_label = None
    return ir


def disagreements(ir: IR) -> list[SignatureLabel]:
    """Prior vs model. The richest signal about whether the matrix is right."""
    return [s for s in ir.signatures if s.agreement is False]

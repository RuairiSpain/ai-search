"""Typed data model for the pattern compiler.

Everything downstream of intake reads these structures, never the original
prose. Prose is parsed exactly once (see intake.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# --------------------------------------------------------------------------
# Provenance. Every IR field is either cited to the source document or an
# explicit assumption with a blast radius. A field with neither is a bug.
# --------------------------------------------------------------------------
class Blast(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Provenance:
    kind: str                      # "source" | "assumed" | "user"
    quote: str | None = None       # source span evidence
    blast_radius: Blast | None = None

    @property
    def is_assumed(self) -> bool:
        return self.kind == "assumed"


@dataclass
class Field_:
    """A {value, provenance} pair. The IR is made of these."""
    value: Any
    provenance: Provenance

    @staticmethod
    def sourced(value: Any, quote: str) -> "Field_":
        return Field_(value, Provenance("source", quote=quote))

    @staticmethod
    def assumed(value: Any, blast: Blast = Blast.MEDIUM) -> "Field_":
        return Field_(value, Provenance("assumed", blast_radius=blast))

    @staticmethod
    def from_user(value: Any) -> "Field_":
        return Field_(value, Provenance("user"))


# --------------------------------------------------------------------------
# Diagnosis
# --------------------------------------------------------------------------
@dataclass
class SignatureLabel:
    signature_id: str
    problem: str
    pattern: str | None            # catalogue pattern id, or None for advisory
    advisory: str | None
    prior_score: float             # deterministic evidence prior, 0..1
    prior_label: bool
    model_label: bool | None = None    # set when an LLM diagnoser runs
    user_label: bool | None = None     # set by the interview
    matched_terms: list[str] = field(default_factory=list)

    @property
    def final_label(self) -> bool:
        """The interview wins over the model, which wins over the prior."""
        if self.user_label is not None:
            return self.user_label
        if self.model_label is not None:
            return self.model_label
        return self.prior_label

    @property
    def agreement(self) -> bool | None:
        if self.model_label is None:
            return None
        return self.model_label == self.prior_label


# --------------------------------------------------------------------------
# The IR
# --------------------------------------------------------------------------
@dataclass
class TaskClass:
    id: str
    kind: str                      # decision | retrieval | action
    description: str = ""
    volume_per_day: int | None = None
    latency_envelope_s: int | None = None


@dataclass
class ToolBinding:
    system: str
    access: str                    # "read" | "write"
    sensitivity: str = "internal"
    residency: str = "unspecified"

    @property
    def is_write(self) -> bool:
        return self.access == "write"


@dataclass
class EvaluatorCandidate:
    task_class: str
    good_looks_like: str
    testability: str               # test_based|rule_based|model_judge|human|hybrid


@dataclass
class IR:
    """The intermediate representation. The load-bearing artefact."""
    name: str = "unnamed"
    objective: dict[str, Field_] = field(default_factory=dict)
    task_classes: list[TaskClass] = field(default_factory=list)
    signatures: list[SignatureLabel] = field(default_factory=list)
    tools: list[ToolBinding] = field(default_factory=list)
    must_be_deterministic: list[str] = field(default_factory=list)
    needs_approval: list[str] = field(default_factory=list)
    human_in_reasoning: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    evaluator_candidates: list[EvaluatorCandidate] = field(default_factory=list)
    source_origins: set[str] = field(default_factory=set)   # bound source types
    sensitive_data: bool = False
    weak_tests: bool = False
    raw_text: str = ""
    injection_flags: list[str] = field(default_factory=list)   # instruction-like spans, kept as data

    # ---- readiness (computed) ----
    @property
    def diagnosed(self) -> list[SignatureLabel]:
        return [s for s in self.signatures if s.final_label]

    @property
    def reasoning_signatures(self) -> list[SignatureLabel]:
        """Signatures that select a pattern, excluding stale_facts and advisory."""
        return [s for s in self.diagnosed
                if s.pattern is not None and s.signature_id != "stale_facts"]

    @property
    def advisory_signatures(self) -> list[SignatureLabel]:
        return [s for s in self.diagnosed if s.pattern is None]

    @property
    def evaluator_named(self) -> bool:
        if not self.evaluator_candidates:
            return False
        if not self.task_classes:
            return bool(self.evaluator_candidates)
        primary = self.task_classes[0].id
        return any(e.task_class == primary for e in self.evaluator_candidates)

    @property
    def all_fields(self) -> list[Field_]:
        return list(self.objective.values())

    # An assumed field's weight is its blast radius: a missing residency does
    # not undermine a recommendation the way a missing success criterion does.
    _BLAST_WEIGHT = {Blast.HIGH: 1.0, Blast.MEDIUM: 0.5, Blast.LOW: 0.15}

    @property
    def unknown_ratio(self) -> float:
        """Blast-weighted share of the IR that was assumed rather than stated."""
        fields = self.all_fields
        if not fields:
            return 1.0
        total = 0.0
        for f in fields:
            if f.provenance.is_assumed:
                total += self._BLAST_WEIGHT.get(f.provenance.blast_radius, 0.5)
        return round(total / len(fields), 3)

    @property
    def unknowns(self) -> list[tuple[str, Field_]]:
        return [(k, v) for k, v in self.objective.items() if v.provenance.is_assumed]

    @property
    def high_blast_unknowns(self) -> int:
        return sum(1 for _, f in self.unknowns
                   if f.provenance.blast_radius == Blast.HIGH)

    @property
    def diagnosis_confident(self) -> bool:
        """At least one signature labelled with real support."""
        if not self.diagnosed:
            return False
        return any(s.prior_score >= 0.15 or s.user_label or s.model_label
                   for s in self.diagnosed)

    @property
    def binds_writes(self) -> bool:
        return any(t.is_write for t in self.tools)


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------
@dataclass
class Node:
    """A node in a composition expression tree.

    A leaf is a pattern. An internal node is one of the five operators.
    Nothing else is expressible, which is the point.
    """
    pattern: str | None = None                 # leaf
    operator: str | None = None                # sequence|nest|guard|fan|substitute
    children: list["Node"] = field(default_factory=list)

    @staticmethod
    def leaf(pattern_id: str) -> "Node":
        return Node(pattern=pattern_id)

    @staticmethod
    def op(operator: str, *children: "Node") -> "Node":
        return Node(operator=operator, children=list(children))

    @property
    def is_leaf(self) -> bool:
        return self.pattern is not None

    def patterns(self) -> list[str]:
        if self.is_leaf:
            return [self.pattern]           # type: ignore[list-item]
        out: list[str] = []
        for c in self.children:
            out.extend(c.patterns())
        return out

    def leaves(self) -> list["Node"]:
        if self.is_leaf:
            return [self]
        out: list[Node] = []
        for c in self.children:
            out.extend(c.leaves())
        return out

    def signature(self) -> str:
        """Structural identity. Two candidates sharing this are the same option."""
        if self.is_leaf:
            return self.pattern            # type: ignore[return-value]
        inner = ",".join(c.signature() for c in self.children)
        return f"{self.operator}({inner})"

    def depth(self) -> int:
        if self.is_leaf:
            return 1
        return 1 + max((c.depth() for c in self.children), default=0)

    def __str__(self) -> str:
        return self.signature()


@dataclass
class Kill:
    rule_id: str
    phase: int
    reason: str
    repair: str | None = None


@dataclass
class Candidate:
    tree: Node
    axis: str = ""                             # minimal | balanced | ambitious
    kills: list[Kill] = field(default_factory=list)
    score: float = 0.0
    cost_per_task: float | None = None
    latency_s: float | None = None
    confidence_band: float = 0.35
    recommended: bool = False
    notes: list[str] = field(default_factory=list)
    warnings: list[Kill] = field(default_factory=list)

    @property
    def alive(self) -> bool:
        return not self.kills

    @property
    def signature(self) -> str:
        return self.tree.signature()


# --------------------------------------------------------------------------
# Outcome ladder
# --------------------------------------------------------------------------
class Outcome(str, Enum):
    BASELINE_RECOMMENDED = "baseline_recommended"
    THREE_CARDS = "three_cards"
    PRIMITIVE_SCAFFOLD = "primitive_scaffold"
    BASELINE_FALLBACK = "baseline_fallback"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Result:
    outcome: Outcome
    tier: int
    verified: bool
    confidence: Confidence
    descent_reason: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    baseline: Candidate | None = None
    scaffold: "Scaffold | None" = None
    questions: list[str] = field(default_factory=list)
    kill_log: list[tuple[str, Kill]] = field(default_factory=list)
    ir: IR | None = None


@dataclass
class Scaffold:
    """A Tier-2 primitive composition. Unverified by construction."""
    primitives: list[str]
    loops: list[str]
    evaluators: list[str]
    dependencies: list[str]
    rationale: str
    unverified_reasons: list[str] = field(default_factory=list)

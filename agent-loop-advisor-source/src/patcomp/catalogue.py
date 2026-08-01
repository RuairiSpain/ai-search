"""The catalogue, as data.

Pattern selection is a lookup with reasoning on top, never retrieval over prose.
Every fact the compiler uses about a pattern comes from its manifest.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import yaml

DEFAULT_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "catalogue")


@dataclass
class Pattern:
    id: str
    name: str
    title: str
    role: str
    summary: str
    problem_signatures: list[str]
    primitives: list[str]
    accepts: list[str]
    produces: str
    evaluator: dict[str, Any]
    budget_profile: dict[str, Any]
    latency_class: str
    cost_class: str
    capabilities: dict[str, bool]
    composes_with: list[str]
    conflicts_with: list[str]
    failure_modes: list[str]
    beats_baseline_when: str
    agents: list[str]
    skills: list[str]
    azure_services: list[str]
    variants: list[str]
    emits: str = "full"
    binds_write_tools: bool = False
    requires_review_gate: bool = False

    # ---- capability helpers ----
    @property
    def is_guard(self) -> bool:
        return bool(self.capabilities.get("guard"))

    @property
    def is_human_gate(self) -> bool:
        return bool(self.capabilities.get("human_gate"))

    @property
    def has_decision_nodes(self) -> bool:
        return bool(self.capabilities.get("decision_nodes"))

    @property
    def has_evaluator_slot(self) -> bool:
        return bool(self.capabilities.get("evaluator_slot"))

    @property
    def has_loop(self) -> bool:
        return bool(self.capabilities.get("loop"))

    @property
    def is_deterministic_core(self) -> bool:
        return bool(self.capabilities.get("deterministic_core"))

    @property
    def is_evaluator(self) -> bool:
        return bool(self.capabilities.get("is_evaluator"))

    @property
    def llm_calls(self) -> int:
        return int(self.budget_profile.get("llm_calls", 0) or 0)

    @property
    def tokens(self) -> int:
        return int(self.budget_profile.get("tokens", 0) or 0)

    @property
    def wall_clock_s(self) -> int:
        return int(self.budget_profile.get("wall_clock_s", 0) or 0)

    @property
    def loop_rounds(self) -> int:
        """Iterations an enclosing loop runs, for nest budget compounding."""
        for key in ("max_rounds", "max_review_rounds", "max_repair_rounds"):
            if key in self.budget_profile:
                return int(self.budget_profile[key])
        return 1


@dataclass
class Signature:
    id: str
    problem: str
    pattern: str | None
    evidence: list[str]
    advisory: str | None = None
    paper_section: int | None = None


@dataclass
class Conflict:
    a: str
    b: str
    relation: str
    reason: str
    severity: str
    when: str | None = None
    unless: str | None = None
    repair: str | None = None


@dataclass
class ContractType:
    name: str
    role: str                      # source|context|decision|artefact|terminal
    fields: list[str]
    produced_by: list[str]
    consumed_by: list[str]

    @property
    def is_terminal(self) -> bool:
        return self.role == "terminal"

    @property
    def is_source(self) -> bool:
        return self.role == "source"


@dataclass
class Adapter:
    id: str
    from_: str
    to: str
    why: str
    severity_if_missing: str = "fatal"


@dataclass
class Rule:
    id: str
    severity: str
    phase: int
    statement: str
    provenance: str = ""


@dataclass
class Catalogue:
    patterns: dict[str, Pattern] = field(default_factory=dict)
    signatures: list[Signature] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    types: dict[str, ContractType] = field(default_factory=dict)
    adapters: list[Adapter] = field(default_factory=list)
    subsumes: list[dict] = field(default_factory=list)
    rules: dict[str, Rule] = field(default_factory=dict)
    operators: list[dict] = field(default_factory=list)
    version: str = "unknown"

    # ---- lookups ----
    def pattern(self, pid: str) -> Pattern:
        return self.patterns[pid]

    def signature(self, sid: str) -> Signature | None:
        for s in self.signatures:
            if s.id == sid:
                return s
        return None

    def patterns_for(self, signature_id: str) -> list[Pattern]:
        return [p for p in self.patterns.values()
                if signature_id in p.problem_signatures]

    def guards(self) -> list[Pattern]:
        return [p for p in self.patterns.values() if p.is_guard]

    def type_of(self, name: str) -> ContractType | None:
        return self.types.get(name)

    def adapter_between(self, frm: str, to: str) -> Adapter | None:
        for a in self.adapters:
            if a.from_ == frm and a.to == to:
                return a
        return None

    def rules_for_phase(self, phase: int) -> list[Rule]:
        return [r for r in self.rules.values() if r.phase == phase]

    @property
    def baseline_id(self) -> str:
        return "00"

    # ---- integrity ----
    def validate(self) -> list[str]:
        """Catalogue drift check. Returns problems; empty means healthy."""
        problems: list[str] = []
        for p in self.patterns.values():
            for sid in p.problem_signatures:
                if not self.signature(sid):
                    problems.append(f"pattern {p.id}: unknown signature '{sid}'")
            for t in p.accepts:
                if t not in self.types:
                    problems.append(f"pattern {p.id}: unknown accepted type '{t}'")
            if p.produces not in self.types:
                problems.append(f"pattern {p.id}: unknown produced type '{p.produces}'")
            for other in p.composes_with:
                if other not in self.patterns:
                    problems.append(f"pattern {p.id}: composes_with unknown '{other}'")
        for s in self.signatures:
            if s.pattern and s.pattern not in self.patterns:
                problems.append(f"signature {s.id}: unknown pattern '{s.pattern}'")
        return problems


def _load_yaml(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load(root: str | None = None) -> Catalogue:
    root = os.path.abspath(root or DEFAULT_ROOT)
    cat = Catalogue()

    # patterns
    for path in sorted(glob.glob(os.path.join(root, "patterns", "*.yaml"))):
        d = _load_yaml(path)
        inputs = d.get("inputs") or {}
        outputs = d.get("outputs") or {}
        pid = str(d["id"])
        cat.patterns[pid] = Pattern(
            id=pid,
            name=d.get("name", ""),
            title=d.get("title", ""),
            role=d.get("role", ""),
            summary=(d.get("summary") or "").strip(),
            problem_signatures=d.get("problem_signatures") or [],
            primitives=d.get("primitives") or [],
            accepts=inputs.get("accepts") or [],
            produces=outputs.get("produces") or "",
            evaluator=d.get("evaluator") or {},
            budget_profile=d.get("budget_profile") or {},
            latency_class=d.get("latency_class", ""),
            cost_class=d.get("cost_class", ""),
            capabilities=d.get("capabilities") or {},
            composes_with=[str(x) for x in (d.get("composes_with") or [])],
            conflicts_with=[str(x) for x in (d.get("conflicts_with") or [])],
            failure_modes=d.get("failure_modes") or [],
            beats_baseline_when=(d.get("beats_baseline_when") or "").strip(),
            agents=d.get("agents") or [],
            skills=d.get("skills") or [],
            azure_services=d.get("azure_services") or [],
            variants=d.get("variants") or [],
            emits=d.get("emits", "full"),
            binds_write_tools=bool(d.get("binds_write_tools")),
            requires_review_gate=bool(d.get("requires_review_gate")),
        )

    # signatures
    sd = _load_yaml(os.path.join(root, "signatures.yaml"))
    for s in sd.get("signatures", []):
        cat.signatures.append(Signature(
            id=s["id"], problem=s.get("problem", ""),
            pattern=str(s["pattern"]) if s.get("pattern") else None,
            evidence=s.get("evidence") or [],
            advisory=s.get("advisory"),
            paper_section=s.get("paper_section"),
        ))

    # conflicts
    cd = _load_yaml(os.path.join(root, "conflicts.yaml"))
    for c in cd.get("conflicts", []):
        cat.conflicts.append(Conflict(
            a=str(c["a"]), b=str(c["b"]), relation=c.get("relation", "any"),
            reason=(c.get("reason") or "").strip(), severity=c.get("severity", "warning"),
            when=c.get("when"), unless=c.get("unless"), repair=c.get("repair"),
        ))

    # contracts
    td = _load_yaml(os.path.join(root, "contracts.yaml"))
    for t in td.get("types", []):
        cat.types[t["name"]] = ContractType(
            name=t["name"], role=t.get("role", ""), fields=t.get("fields") or [],
            produced_by=[str(x) for x in (t.get("produced_by") or [])],
            consumed_by=[str(x) for x in (t.get("consumed_by") or [])],
        )
    for a in td.get("adapters") or []:
        cat.adapters.append(Adapter(
            id=a["id"], from_=a["from"], to=a["to"], why=(a.get("why") or "").strip(),
            severity_if_missing=a.get("severity_if_missing", "fatal"),
        ))
    cat.subsumes = td.get("subsumes") or []

    # operators + rules
    od = _load_yaml(os.path.join(root, "operators.yaml"))
    cat.version = str(od.get("version", "unknown"))
    cat.operators = od.get("operators") or []
    for r in od.get("legality_rules", []):
        cat.rules[r["id"]] = Rule(
            id=r["id"], severity=r.get("severity", "warning"),
            phase=int(r.get("phase", 1)), statement=(r.get("statement") or "").strip(),
            provenance=r.get("provenance", ""),
        )
    return cat


@lru_cache(maxsize=4)
def default() -> Catalogue:
    return load()

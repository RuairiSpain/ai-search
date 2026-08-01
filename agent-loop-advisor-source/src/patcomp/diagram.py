"""Mermaid + markdown diagrams for patterns and compositions.

Two kinds:
  1. Per-pattern canonical diagrams — the shape each pattern has in the paper,
     rendered as `flowchart LR` (the style used in the field guide).
  2. Composition diagrams — the recommended expression tree, wired by operator
     semantics (sequence chains, guard gates the action boundary, nest occupies
     a decision node, fan expands then merges).

Every pattern is a variation of one control loop (generate -> evaluate ->
select -> repair -> escalate); the per-pattern diagrams make that visible.
"""
from __future__ import annotations

from .catalogue import Catalogue
from .models import Node

# --------------------------------------------------------------------------
# Per-pattern canonical diagrams. Authored from each manifest's summary so the
# shape matches the paper. Kept as flowchart LR bodies (without the fence) so
# they can be embedded or emitted standalone.
# --------------------------------------------------------------------------
_PATTERN_FLOW: dict[str, str] = {
    "00": """flowchart LR
    Q[Question] --> R[Retrieve<br/>grounded source]
    R --> A[Answer<br/>with citations]""",
    "01": """flowchart LR
    Q[Question / Case] --> G[Generate<br/>candidate answers]
    G --> E[Evaluate<br/>against criteria]
    E --> S[Select one]
    S --> D[Decision]
    E -.rejected.-> L[(Log rejected<br/>alternatives)]""",
    "02": """flowchart LR
    Q[Question / Case] --> T[Thought]
    T --> A[Action<br/>call a tool]
    A --> O[Observation]
    O -->|under budget| T
    O --> D[Decision]""",
    "03": """flowchart LR
    Q[Question / Case] --> P[Planner<br/>decompose]
    P --> W1[Worker<br/>small model]
    P --> W2[Worker<br/>small model]
    W1 --> RV[Reviewer<br/>different family]
    W2 --> RV
    RV --> M[Merger] --> D[Decision]""",
    "04": """flowchart LR
    C[Case] --> MP[Model<br/>interprets &amp; proposes]
    MP --> RE{Rules engine<br/>decides}
    RE -->|verdict wins| D[Decision]
    RE -.cites.-> RU[(Rule that<br/>triggered)]""",
    "05": """flowchart LR
    Q[Question / Case] --> H1[Hypothesis 1]
    Q --> H2[Hypothesis 2]
    Q --> H3[Hypothesis 3]
    H1 --> EV[Test against<br/>evidence]
    H2 --> EV
    H3 --> EV
    EV -->|prune| SY[Synthesise<br/>survivor]
    SY --> D[Decision]""",
    "06": """flowchart LR
    Q[Question / Case] --> SM[Semantic<br/>memory]
    Q --> EM[Episodic<br/>memory]
    Q --> PM[Procedural<br/>memory]
    SM --> TR{Security trim<br/>&amp; TTL}
    EM --> TR
    PM --> TR
    TR --> MC[MemoryContext]""",
    "07": """flowchart LR
    R[Completed run<br/>+ trace] --> RF[Reflect]
    RF --> SA[Author<br/>candidate skill]
    SA --> RG{Review gate}
    RG -->|approved| LIB[(Skill library)]
    LIB --> NR[Improved<br/>next run]""",
    "08": """flowchart LR
    IN[Intake<br/>state] --> AS[Assessment<br/>state]
    AS --> EX{Exception?}
    EX -->|yes| HR[Human review<br/>state]
    EX -->|no| PAY[Payment state<br/>rules, no LLM]
    HR --> PAY
    PAY --> D[Decision]""",
    "09": """flowchart LR
    Q[Question / Case] --> B[Breadth<br/>cheap model]
    B --> K{Constraint kill<br/>free}
    K -->|survivors| DP[Deepen<br/>survivors]
    DP --> SEQ[Sequence<br/>ordered plan]""",
    "10": """flowchart LR
    Q[Question / Case] --> PL[Plan<br/>traversal]
    PL --> W[Walk<br/>entity graph]
    W -->|more hops| W
    W --> CH[Cite<br/>evidence chain]
    CH --> GC[GraphContext]""",
    "11": """flowchart LR
    S[Spec / Case] --> GEN[Generate<br/>artefact]
    GEN --> T{Run tests}
    T -->|fail| AN[Analyse failure]
    AN --> RP[Repair]
    RP --> GEN
    T -->|pass, capped| ART[Artefact]""",
    "12": """flowchart LR
    REQ[Requirements] --> DIA[Diagnose]
    DIA --> GENC[Generate<br/>candidates]
    GENC --> KILL{Constraint kill}
    KILL --> PRES[Present 3<br/>+ baseline]
    PRES --> EM[Emit &amp; verify]""",
    "13": """flowchart LR
    IN[Decision / Artefact] --> HR{Human review<br/>SLA}
    HR -->|approve| OUT[Approved<br/>Decision]
    HR -->|reject| ESC[Escalate<br/>or revise]""",
}

# The control-loop role each pattern plays, for the markdown caption.
_LOOP_ROLE = {
    "00": "grounding — retrieve and answer, no loop",
    "01": "generate candidates, evaluate, select",
    "02": "act and observe, under a budget",
    "03": "decompose, fan out, review, merge",
    "04": "propose, then a deterministic engine decides",
    "05": "branch hypotheses, evaluate, prune",
    "06": "recall, scope and trim, provide context",
    "07": "run, reflect, author, review, improve",
    "08": "a durable state machine with agent decision points",
    "09": "breadth, free constraint kill, deepen survivors",
    "10": "plan traversals, walk the graph, cite the chain",
    "11": "generate, test, analyse, repair, under a cap",
    "12": "diagnose, compose, kill, present, emit",
    "13": "a human occupies a named point, with an SLA",
}


def pattern_mermaid(pattern_id: str) -> str:
    return _PATTERN_FLOW.get(pattern_id, f"""flowchart LR
    IN[Input] --> P[Pattern {pattern_id}] --> OUT[Output]""")


def pattern_markdown(pattern_id: str, cat: Catalogue) -> str:
    p = cat.pattern(pattern_id)
    lines = [
        f"### Pattern {p.id} — {p.title}",
        "",
        f"*{p.summary.strip()}*",
        "",
        f"**Control loop:** {_LOOP_ROLE.get(p.id, 'a variation of the control loop')}.",
        f"**Beats a baseline when:** {p.beats_baseline_when}",
        "",
        "```mermaid",
        pattern_mermaid(p.id),
        "```",
        "",
        f"- **Accepts:** {', '.join(p.accepts)} → **produces:** {p.produces}",
        f"- **Evaluator:** {p.evaluator.get('type')} on {p.evaluator.get('target')}",
        f"- **Known failure modes:** {', '.join(p.failure_modes)}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Composition diagrams. Walk the operator tree into a single flowchart.
# --------------------------------------------------------------------------
class _Builder:
    def __init__(self, cat: Catalogue):
        self.cat = cat
        self.lines: list[str] = []
        self.n = 0

    def _id(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}{self.n}"

    def _label(self, pid: str) -> str:
        p = self.cat.pattern(pid)
        short = p.title.split("(")[0].strip()
        # keep labels short and mermaid-safe
        short = short.replace("and ", "").replace("  ", " ")
        return f'{pid} {short}'[:34]

    def walk(self, node: Node) -> tuple[str, str]:
        """Return (entry_id, exit_id), appending lines. Handles all 5 operators."""
        if node.is_leaf:
            nid = self._id("P")
            self.lines.append(f'    {nid}["{self._label(node.pattern)}"]')
            return nid, nid

        op = node.operator
        if op == "sequence":
            first_entry = None
            prev_exit = None
            for child in node.children:
                e, x = self.walk(child)
                if first_entry is None:
                    first_entry = e
                if prev_exit is not None:
                    self.lines.append(f"    {prev_exit} --> {e}")
                prev_exit = x
            return first_entry, prev_exit

        if op == "guard":
            target, guard = node.children
            te, tx = self.walk(target)
            gid = self._id("G")
            glabel = self._label(guard.pattern) if guard.is_leaf else "guard"
            self.lines.append(f'    {gid}{{"{glabel}<br/>verdict wins"}}')
            self.lines.append(f"    {tx} --> {gid}")
            return te, gid

        if op == "nest":
            outer, inner = node.children
            oe, ox = self.walk(outer)
            sub = self._id("sg")
            self.lines.append(f"    subgraph {sub}[nested at a decision node]")
            ie, ix = self.walk(inner)
            self.lines.append("    end")
            self.lines.append(f"    {ox} -->|decision node| {ie}")
            done = self._id("N")
            self.lines.append(f'    {done}["result"]')
            self.lines.append(f"    {ix} --> {done}")
            return oe, done

        if op == "fan":
            plan, worker = node.children
            pe, px = self.walk(plan)
            we, wx = self.walk(worker)
            self.lines.append(f"    {px} -->|fan out x3| {we}")
            merge = self._id("M")
            self.lines.append(f'    {merge}["merge"]')
            self.lines.append(f"    {wx} --> {merge}")
            return pe, merge

        if op == "substitute":
            target, ev = node.children
            te, tx = self.walk(target)
            eid = self._id("E")
            elabel = self._label(ev.pattern) if ev.is_leaf else "evaluator"
            self.lines.append(f'    {eid}[["{elabel}<br/>replaces evaluator"]]')
            self.lines.append(f"    {tx} -.evaluator.-> {eid}")
            return te, eid

        # unknown operator: render children in sequence as a fallback
        e0 = None
        px = None
        for c in node.children:
            e, x = self.walk(c)
            if e0 is None:
                e0 = e
            if px is not None:
                self.lines.append(f"    {px} --> {e}")
            px = x
        return e0, px


def composition_mermaid(node: Node, cat: Catalogue) -> str:
    b = _Builder(cat)
    entry, exit_ = b.walk(node)
    header = "flowchart LR"
    start = f'    START(["input"]) --> {entry}'
    end = f'    {exit_} --> DONE(["output"])'
    return "\n".join([header, start] + b.lines + [end])


def composition_markdown(node: Node, cat: Catalogue, title: str = "Recommended composition") -> str:
    pats = []
    for p in node.patterns():
        if p not in pats:
            pats.append(p)
    lines = [
        f"## {title}",
        "",
        f"**Composition:** `{node.signature()}`",
        "",
        "```mermaid",
        composition_mermaid(node, cat),
        "```",
        "",
        "### Patterns in this composition",
        "",
    ]
    for pid in pats:
        lines.append(pattern_markdown(pid, cat))
        lines.append("")
    return "\n".join(lines)


def primitives_mermaid(primitives: list[str]) -> str:
    """A simple left-to-right flow of the primitive loops in a scaffold."""
    if not primitives:
        return "flowchart LR\n    IN[input] --> OUT[output]"
    lines = ["flowchart LR", "    START([input])"]
    prev = "START"
    for i, p in enumerate(primitives):
        nid = f"S{i}"
        lines.append(f'    {nid}["{p}"]')
        lines.append(f"    {prev} --> {nid}")
        prev = nid
    lines.append(f"    {prev} --> DONE([output, UNVERIFIED])")
    return "\n".join(lines)

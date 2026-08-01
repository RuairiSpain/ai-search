"""patcomp — the pattern compiler CLI.

    patcomp compile requirements.md      interview + recommendation
    patcomp compile FILE --no-interview  document only, no questions
    patcomp ask                          interview only, no document
    patcomp catalogue                    show the catalogue and its integrity
    patcomp explain 05                   what a pattern is and when it wins
    patcomp goldenset                    run the golden set, report accuracy
    patcomp goldenset --cohort=validation   held-out cases only — the number to quote
    patcomp audit-signatures             check evidence lists for cross-signature firing
"""
from __future__ import annotations

import argparse
import sys

from . import catalogue as cat_mod
from . import present
from . import diagram, emit
from .models import Outcome
from .pipeline import compile_requirements


def _tty_asker():
    def ask(prompt: str, options: list[str], default: int) -> int:
        print()
        print(f"  {prompt}")
        for i, o in enumerate(options):
            mark = "*" if i == default else " "
            print(f"    {mark} [{i + 1}] {o}")
        try:
            raw = input(f"  choose 1-{len(options)} (enter = {default + 1}): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        if not raw:
            return default
        try:
            return int(raw) - 1
        except ValueError:
            return default
    return ask


def cmd_compile(args) -> int:
    cat = cat_mod.load(args.catalogue) if args.catalogue else cat_mod.default()
    if args.file == "-":
        text = sys.stdin.read()
    else:
        with open(args.file, "r", encoding="utf-8") as fh:
            text = fh.read()

    ask = None if args.no_interview else _tty_asker()
    if not args.no_interview:
        print()
        print("A few questions. Defaults are pre-selected — press enter to accept.")
    result, trace = compile_requirements(text, cat, name=args.name, ask=ask)

    print()
    print(present.render(result, cat))
    if args.diagram:
        print()
        print(_diagram_for(result, cat))
    print()
    print(f"[outcome={result.outcome.value} tier={result.tier} "
          f"verified={result.verified} confidence={result.confidence.value}"
          + (f" descent={result.descent_reason}" if result.descent_reason else "") + "]")
    if args.verbose:
        print()
        print(present.render_kill_log(result))
        if trace.ir_problems:
            print()
            print("Intake notes:")
            for p in trace.ir_problems:
                print(f"  - {p}")
    return 0


def cmd_ask(args) -> int:
    """Interview-only mode: no document, just the architect's answers."""
    cat = cat_mod.load(args.catalogue) if args.catalogue else cat_mod.default()
    print()
    print("Describe the problem in a sentence or two, then press enter twice.")
    lines = []
    try:
        while True:
            line = input("  > ").strip()
            if not line:
                break
            lines.append(line)
    except (EOFError, KeyboardInterrupt):
        pass
    text = " ".join(lines) or "unspecified"
    result, _ = compile_requirements(text, cat, name="interview", ask=_tty_asker())
    print()
    print(present.render(result, cat))
    if args.diagram:
        print()
        print(_diagram_for(result, cat))
    print()
    print(f"[outcome={result.outcome.value} tier={result.tier} "
          f"confidence={result.confidence.value}]")
    return 0


def cmd_catalogue(args) -> int:
    cat = cat_mod.load(args.catalogue) if args.catalogue else cat_mod.default()
    print(f"Catalogue — operators v{cat.version}")
    print(f"  {len(cat.patterns)} patterns, {len(cat.signatures)} signatures, "
          f"{len(cat.conflicts)} conflicts, {len(cat.types)} contract types, "
          f"{len(cat.rules)} legality rules")
    print()
    for pid in sorted(cat.patterns):
        p = cat.pattern(pid)
        print(f"  {pid}  {p.title[:52]:54s} {p.cost_class:6s} {p.latency_class}")
    print()
    problems = cat.validate()
    if problems:
        print("INTEGRITY PROBLEMS:")
        for x in problems:
            print(f"  - {x}")
        return 1
    print("Integrity: clean")
    return 0


def cmd_explain(args) -> int:
    cat = cat_mod.load(args.catalogue) if args.catalogue else cat_mod.default()
    pid = args.pattern
    if pid not in cat.patterns:
        print(f"No pattern '{pid}'. Try: patcomp catalogue")
        return 1
    p = cat.pattern(pid)
    print(f"{p.id}  {p.title}")
    print(f"  {p.summary.strip()}")
    print()
    print(f"  Beats baseline when : {p.beats_baseline_when}")
    print(f"  Answers             : {', '.join(p.problem_signatures)}")
    print(f"  Primitives          : {', '.join(p.primitives) or '—'}")
    print(f"  Accepts / produces  : {', '.join(p.accepts)} -> {p.produces}")
    print(f"  Evaluator           : {p.evaluator.get('type')} on {p.evaluator.get('target')}")
    print(f"  Budget              : {p.llm_calls} calls, {p.tokens} tokens, {p.wall_clock_s}s")
    print(f"  Composes with       : {', '.join(p.composes_with) or '—'}")
    print(f"  Known failures      : {', '.join(p.failure_modes)}")
    print(f"  Agents / skills     : {', '.join(p.agents)} / {', '.join(p.skills) or '—'}")
    return 0


def cmd_goldenset(args) -> int:
    from .goldenset import run_golden_set, report
    cat = cat_mod.load(args.catalogue) if args.catalogue else cat_mod.default()
    rows = run_golden_set(cat)
    print(report(rows, cohort=args.cohort))
    return 0


def cmd_audit_signatures(args) -> int:
    """Informational, not a gate: diagnosis is deliberately multi-label, so a
    finding here means "a human should look at this," not "the build is
    broken." The regression guard that fails CI on a NEW, unreviewed
    collision lives in tests/test_patcomp.py, not in this exit code."""
    from . import signature_audit
    cat = cat_mod.load(args.catalogue) if args.catalogue else cat_mod.default()
    print(signature_audit.report(cat))
    return 0


def _diagram_for(result, cat) -> str:
    from .models import Outcome
    if result.outcome is Outcome.THREE_CARDS and result.candidates:
        rec = next((c for c in result.candidates if c.axis == "balanced"),
                   result.candidates[0])
        return "```mermaid\n" + diagram.composition_mermaid(rec.tree, cat) + "\n```"
    if result.scaffold is not None:
        return "```mermaid\n" + diagram.primitives_mermaid(result.scaffold.primitives) + "\n```"
    return "```mermaid\n" + diagram.pattern_mermaid("00") + "\n```"


def cmd_diagram(args) -> int:
    cat = cat_mod.load(args.catalogue) if args.catalogue else cat_mod.default()
    if args.pattern:
        if args.pattern not in cat.patterns:
            print(f"No pattern '{args.pattern}'."); return 1
        print(diagram.pattern_markdown(args.pattern, cat))
    elif args.composition:
        from .tools_parse import parse_composition
        try:
            node = parse_composition(args.composition, cat)
        except ValueError as e:
            print(f"Invalid composition: {e}")
            return 1
        print(diagram.composition_markdown(node, cat))
    else:
        print("Give --pattern ID or --composition EXPR"); return 1
    return 0


def cmd_emit(args) -> int:
    cat = cat_mod.load(args.catalogue) if args.catalogue else cat_mod.default()
    if args.all:
        files = emit.emit_catalogue_project(cat)
        label = "full catalogue"
    else:
        if not args.file:
            print("Give a requirements file, or --all for the whole catalogue.")
            return 1
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        result, _ = compile_requirements(text, cat, answers=None)
        files = emit.emit_solution_project(result, cat, args.name)
        label = f"solution for outcome={result.outcome.value}"
    if args.zip:
        data = emit.zip_bytes(files, top=args.name)
        with open(args.zip, "wb") as fh:
            fh.write(data)
        print(f"Emitted {label}: {len(files)} files -> {args.zip}")
    else:
        out = args.out or "foundry-project"
        emit.write_files(files, out)
        print(f"Emitted {label}: {len(files)} files -> {out}/")
        print(f"Verify with: python {out}/verify_structure.py")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="patcomp",
        description="Turn a requirements document into a pattern recommendation.")
    ap.add_argument("--catalogue", help="path to a catalogue directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compile", help="compile a requirements document")
    c.add_argument("file", help="path to the document, or - for stdin")
    c.add_argument("--name", default="harness")
    c.add_argument("--no-interview", action="store_true",
                   help="document only; take every default")
    c.add_argument("-v", "--verbose", action="store_true",
                   help="show the kill log and intake notes")
    c.add_argument("--diagram", action="store_true",
                   help="print a mermaid diagram of the recommendation")
    c.set_defaults(func=cmd_compile)

    a = sub.add_parser("ask", help="interview only, no document")
    a.set_defaults(func=cmd_ask)

    g = sub.add_parser("catalogue", help="show the catalogue and check integrity")
    g.set_defaults(func=cmd_catalogue)

    e = sub.add_parser("explain", help="explain one pattern")
    e.add_argument("pattern")
    e.set_defaults(func=cmd_explain)

    s = sub.add_parser("goldenset", help="run the golden set and report accuracy")
    s.add_argument("--cohort", choices=["tuning", "validation"], default=None,
                   help="report one cohort only; omit to see everything mixed "
                        "(fine for a local look, never for a number you quote)")
    s.set_defaults(func=cmd_goldenset)

    au = sub.add_parser("audit-signatures", help="check signature evidence lists for cross-firing")
    au.set_defaults(func=cmd_audit_signatures)

    d = sub.add_parser("diagram", help="print a pattern or composition diagram")
    d.add_argument("--pattern", help="a pattern id, e.g. 05")
    d.add_argument("--composition", help="e.g. 'guard(sequence(10,05),04)'")
    d.set_defaults(func=cmd_diagram)

    em = sub.add_parser("emit", help="emit a Foundry/MAF project scaffold")
    em.add_argument("file", nargs="?", help="requirements file, or - for stdin")
    em.add_argument("--all", action="store_true", help="emit the whole catalogue")
    em.add_argument("--name", default="patcomp-foundry", help="project name")
    em.add_argument("--out", help="output directory")
    em.add_argument("--zip", help="write a .zip to this path instead of a directory")
    em.set_defaults(func=cmd_emit)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

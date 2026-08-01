"""patcomp — the pattern compiler.

Requirements document in, a pattern recommendation out: either a verified
composition from the catalogue, or an unverified primitive scaffold with its
loops, evaluators and dependencies named, or the grounded baseline when the
document does not support a design.

The whole reasoning core is deterministic. An LLM diagnoser can be plugged in
at diagnose.ModelDiagnoser, but nothing requires one.
"""
from .catalogue import Catalogue, load as load_catalogue, default as default_catalogue
from .models import (IR, Candidate, Confidence, Node, Outcome, Result, Scaffold)
from .pipeline import compile_requirements

__version__ = "0.1.0"
__all__ = [
    "Catalogue", "load_catalogue", "default_catalogue", "compile_requirements",
    "IR", "Candidate", "Node", "Result", "Scaffold", "Outcome", "Confidence",
]

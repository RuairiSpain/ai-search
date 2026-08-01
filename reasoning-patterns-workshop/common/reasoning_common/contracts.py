"""Typed contracts between agents (§12: free-text handoffs are where
multi-agent systems silently fail). Shared so reviewer and workers can't drift."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SubTask(BaseModel):
    id: str
    kind: Literal["retrieve", "analyze", "compute"]
    instruction: str
    depends_on: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    goal: str
    subtasks: list[SubTask]
    rationale: str


class WorkerOutput(BaseModel):
    subtask_id: str
    result: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class Review(BaseModel):
    verdict: Literal["approve", "revise", "reject"]
    issues: list[str] = Field(default_factory=list)
    revised_guidance: str = ""


class Decision(BaseModel):
    recommendation: str
    evidence: list[str]
    rejected_alternatives: list[str] = Field(default_factory=list)
    rules_cited: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)

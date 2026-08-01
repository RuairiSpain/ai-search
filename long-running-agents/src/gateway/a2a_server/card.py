"""Builds a conformant a2a-sdk AgentCard from our own AppConfig +
UpstreamAdapter.capabilities. Replaces the old hand-rolled agent_card
endpoint's custom `extensions: {progressFidelity, steering, ...}` blob —
inventing top-level AgentCard fields isn't conformant, so that capability
detail is dropped here rather than smuggled in. A proper A2A extension
declaration is future work if a client genuinely needs to discover it
programmatically; for now it's documented in docs/01, not on the wire.
"""
from __future__ import annotations

from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard

from gateway.config import AppConfig
from gateway.upstream.base import Capabilities


def build_agent_card(app_cfg: AppConfig, capabilities: Capabilities) -> AgentCard:
    return AgentCard(
        name=app_cfg.name,
        description=app_cfg.card.description,
        version="1.0.0",
        capabilities=AgentCapabilities(
            streaming=app_cfg.card.capabilities.streaming,
            push_notifications=app_cfg.card.capabilities.pushNotifications,
        ),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[],
    )

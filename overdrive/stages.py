"""Single pipeline shared by UI, validation, imports and MCP schemas."""

from typing import Literal, get_args

Stage = Literal["New Lead", "Briefing Ready", "Proposal Ready", "Proposal Sent", "Negotiation", "Contract"]
STAGES = list(get_args(Stage))
LEGACY_STAGES = {
    "Lead": "New Lead",
    "Qualificação": "Briefing Ready",
    "Proposta": "Proposal Ready",
    "Negociação": "Negotiation",
}

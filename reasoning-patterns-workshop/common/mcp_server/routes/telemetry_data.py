"""Market/ops data tools (pattern 03 workers)."""
from __future__ import annotations

SEGMENTS = {
    "EU-retail": {"churn_rate": 0.11, "nps": 31, "arr_usd": 12_400_000},
    "US-retail": {"churn_rate": 0.07, "nps": 44, "arr_usd": 20_100_000},
    "EU-enterprise": {"churn_rate": 0.04, "nps": 52, "arr_usd": 33_000_000},
}


def register(mcp):
    @mcp.tool()
    def get_segment_metrics(segment: str) -> dict:
        """Fetch churn, NPS and ARR for a business segment (e.g. 'EU-retail')."""
        return SEGMENTS.get(segment, {"error": f"unknown segment {segment}",
                                      "known": list(SEGMENTS)})

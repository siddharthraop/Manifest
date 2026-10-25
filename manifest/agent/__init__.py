"""The MANIFEST agent: Gemini proposes a mapping rule for a topic that fails a check.

The checks decide. The agent can read the failure, try a fix (at most twice) and open a review
issue. A fix that passes the checks becomes a *proposed* rule; a writer approves it.
"""

DEFAULT_MODEL = "gemini-3.8-flash"  # design doc §5; stable since 2026-09-02 (ADR 0007)
FALLBACK_MODEL = "gemini-3.5-flash"  # stable, no shutdown before 2027-05-19

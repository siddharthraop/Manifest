"""The MANIFEST agent: Gemini proposes a mapping rule for a topic that fails a check.

The checks decide. The agent can read the failure, try a fix (at most twice) and open a review
issue. A fix that passes the checks becomes a *proposed* rule; a writer approves it.
"""

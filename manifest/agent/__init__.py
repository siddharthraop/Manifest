"""The MANIFEST agent: Gemini proposes a mapping rule for a topic that fails a check.

The checks decide. The agent can read the failure, try a fix (at most twice) and open a review
issue. A fix that passes the checks becomes a *proposed* rule; a writer approves it.
"""

from __future__ import annotations

from collections.abc import Mapping

DEFAULT_MODEL = "gemini-3.8-flash"  # design doc §5; stable since 2026-09-02 (ADR 0007)
FALLBACK_MODEL = "gemini-3.5-flash"  # stable, no shutdown before 2027-05-19


KEY_VARS = ("GOOGLE_API_KEY", "GEMINI_API_KEY")


def check_api_key(env: Mapping[str, str]) -> None:
    """Fail early, and without showing the value, if the key is missing or isn't shaped like a key.

    API keys are one unbroken run of letters, digits, '-' or '_'. A value with spaces, line breaks
    or the wrong length is something else pasted by mistake (a 25 Oct 2026 run stored copied
    instructions instead of the key), and an HTTP library may echo it back in an error.
    """
    import re

    from manifest.agent.models import ModelError

    value = next((env[k] for k in KEY_VARS if env.get(k)), "")
    if not value:
        raise ModelError(
            "live mode needs a Gemini API key in GOOGLE_API_KEY or GEMINI_API_KEY, for example:\n"
            '  GOOGLE_API_KEY="$(gcloud secrets versions access latest --secret=gemini-api-key)" \\\n'
            "  manifest fix ..."
        )
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", value.strip()):
        raise ModelError(
            f"the Gemini API key isn't shaped like a key ({len(value)} characters, with spaces, line breaks "
            "or other symbols). Check the gemini-api-key secret; the value is not shown here."
        )


def redact(text: str, env: Mapping[str, str]) -> str:
    """Remove key values from text that may be stored or printed (errors can echo request headers)."""
    for k in KEY_VARS:
        value = env.get(k, "")
        for piece in {value, value.strip(), repr(value.encode())[2:-1], *value.split()}:
            if len(piece) >= 8:
                text = text.replace(piece, "[redacted]")
    return text

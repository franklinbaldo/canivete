import re

FATAL_PATTERNS = [
    (
        re.compile(r"RESOURCE_EXHAUSTED", re.IGNORECASE),
        "rate_limit",
        "Quota / rate limit hit on the upstream model API.",
    ),
    (
        re.compile(r"monthly spending cap", re.IGNORECASE),
        "spending_cap",
        "Monthly spending cap exceeded for the model API key.",
    ),
    (
        re.compile(r"429 Too Many Requests", re.IGNORECASE),
        "rate_limit",
        "Upstream returned HTTP 429.",
    ),
    (
        re.compile(r"Quota exceeded", re.IGNORECASE),
        "quota",
        "API quota exceeded.",
    ),
    (
        re.compile(r"PERMISSION_DENIED", re.IGNORECASE),
        "auth",
        "Auth/permission denied — check OAuth/API key.",
    ),
    (
        re.compile(r"UNAUTHENTICATED", re.IGNORECASE),
        "auth",
        "Auth missing or expired.",
    ),
]

SUGGESTIONS = {
    "rate_limit": "Check the upstream provider's quota dashboard.",
    "quota": "Check the upstream provider's quota dashboard.",
    "spending_cap": "Visit ai.studio/spend to raise the project cap, or remove `GEMINI_API_KEY` from the container env to fall back to OAuth.",
    "auth": "Re-auth: check `oauth_creds.json` mount or `*_API_KEY` env var.",
    "timeout": "Subprocess hit AGENT_TIMEOUT. Likely retry-loop on the provider side.",
}

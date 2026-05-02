from canivete.bot.backends.base import Backend
from canivete.bot.backends.claude_code import ClaudeCodeBackend
from canivete.bot.backends.gemini_cli import GeminiCliBackend

REGISTRY: dict[str, type[Backend]] = {
    "gemini-cli": GeminiCliBackend,
    "claude-code": ClaudeCodeBackend,
}

ALIASES: dict[str, str] = {
    "gemini": "gemini-cli",
    "claude": "claude-code",
}


def normalize_backend_name(name: str) -> str:
    return ALIASES.get(name.strip().lower(), name.strip().lower())

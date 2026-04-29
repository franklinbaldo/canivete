from canivete.bot.backends.base import Backend
from canivete.bot.backends.claude_code import ClaudeCodeBackend
from canivete.bot.backends.gemini_cli import GeminiCliBackend
from canivete.bot.backends.kilo import KiloBackend

REGISTRY: dict[str, type[Backend]] = {
    "gemini-cli": GeminiCliBackend,
    "claude-code": ClaudeCodeBackend,
    "kilo": KiloBackend,
}

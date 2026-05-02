import os
import time

# --- Configurações globais extraídas do bot.py original ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED_USERS = set(filter(None, os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")))

# Modelo e Fallback Chain
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "")
MODEL_CHAIN = [m.strip() for m in os.environ.get("GEMINI_MODEL_CHAIN", DEFAULT_MODEL).split(",") if m.strip()]
MODEL_429_COOLDOWN = int(os.environ.get("GEMINI_429_COOLDOWN_SECONDS", "900"))

# Estado de cooldown: model_name -> unix_ts
_model_cooldown_until: dict[str, float] = {}

def get_next_available_model(skip: set[str] | None = None) -> str | None:
    skip = skip or set()
    now = time.time()
    for m in MODEL_CHAIN:
        if m in skip:
            continue
        if _model_cooldown_until.get(m, 0) > now:
            continue
        return m
    return None

def mark_model_cooldown(model: str):
    if model:
        _model_cooldown_until[model] = time.time() + MODEL_429_COOLDOWN

# Outras envs
WORKSPACE = os.environ.get("WORKSPACE", "/workspace")
AGENT_ROOT = os.environ.get("AGENT_ROOT", "/agent-root")
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "300"))
WHISPER_URL = os.environ.get("WHISPER_URL", "http://litellm:4000")

# Mode: 'streaming' (edit message) ou 'burst' (new message per block)
# Burst é mais estável contra rate limits e erros de MD.
BOT_MODE = os.environ.get("CANIVETE_BOT_MODE", "burst").lower()

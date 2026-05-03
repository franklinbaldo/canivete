import os

# Mode: 'streaming' (edit message), 'burst' (buffered messages) or 'events' (one message per event)
# 'events' is the new default for higher visibility of the agent process.
BOT_MODE = os.environ.get("CANIVETE_BOT_MODE", "events").lower()

# Timeouts
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "600"))
AGENT_INACTIVITY_TIMEOUT = int(os.environ.get("AGENT_INACTIVITY_TIMEOUT", "60"))

# Cache limits
MAX_EDIT_CACHE = 1000

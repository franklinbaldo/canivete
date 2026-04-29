def handle_dynamic_command(text: str, first_name: str) -> str | None:
    if not text.startswith("/"):
        return None

    cmd = text.split(" ", maxsplit=1)[0]

    static_commands = {"/cancel", "/status", "/cron", "/reset", "/new", "/config"}

    if cmd in static_commands:
        return None

    return f"[{first_name} invoked {cmd}]"

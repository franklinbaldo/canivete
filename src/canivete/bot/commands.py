def handle_dynamic_command(text: str, first_name: str) -> str | None:
    if not text.startswith("/"):
        return None

    cmd = text.split(" ", maxsplit=1)[0]

    static_commands = {
        "/backend",
        "/cancel",
        "/config",
        "/cron",
        "/fork",
        "/harness",
        "/new",
        "/reload",
        "/reset",
        "/spawn",
        "/status",
        "/update",
    }

    if cmd in static_commands:
        return None

    return f"[{first_name} invoked {cmd}]"

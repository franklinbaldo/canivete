import json

import telegramify_markdown

from canivete.bot.backends.base import BackendEvent


def render_event(event: BackendEvent) -> str:
    if event.kind == "text":
        return telegramify_markdown.markdownify(event.text)
    if event.kind == "tool_call":
        args_str = json.dumps(event.args)
        return telegramify_markdown.markdownify(f"🔧 **{event.tool}** `{args_str}`")
    if event.kind == "tool_result":
        status = "✅" if event.ok else "❌"
        output_str = event.output or ""
        if len(output_str) > 100:
            output_str = output_str[:97] + "..."
        return telegramify_markdown.markdownify(f"{status} *Result:* `{output_str}`")
    if event.kind == "thought":
        subject = event.subject or "Thinking"
        desc = event.description or ""
        import jinja2
        template = jinja2.Template("🤔 **{{ subject }}**\n_{{ desc }}_")
        rendered = template.render(subject=subject, desc=desc)
        return telegramify_markdown.markdownify(rendered)
    if event.kind == "error":
        return telegramify_markdown.markdownify(f"⚠️ **Error:** {event.message}")
    if event.kind == "stats":
        return telegramify_markdown.markdownify(f"📊 *Stats:* {event.duration_ms}ms, in: {event.tokens_in}, out: {event.tokens_out}")
    if event.kind == "done":
        return telegramify_markdown.markdownify(f"🏁 *Done.* Session: `{event.session_id}`")
    return ""

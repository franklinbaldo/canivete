import json
from typing import Any

import telegramify_markdown

from canivete.bot.backends.base import BackendEvent


def _render_text(event: Any) -> str:
    return telegramify_markdown.markdownify(event.text)

def _render_thought(event: Any) -> str:
    subject = event.subject or "Thinking"
    desc = event.description or ""
    return telegramify_markdown.markdownify(f"💭 *{subject}*\n\n{desc}")

def _render_tool_call(event: Any) -> str:
    args_str = json.dumps(event.args, indent=2, ensure_ascii=False)
    return telegramify_markdown.markdownify(f"🛠️ **EXECUTE: {event.tool}**\n```json\n{args_str}\n```")

def _render_tool_result(event: Any) -> str:
    status = "✅ SUCCESS" if event.ok else "❌ FAILURE"
    output_str = event.output or ""
    if len(output_str) > 2000:
        output_str = output_str[:1997] + "..."
    return telegramify_markdown.markdownify(f"📥 **RESULT: {status}**\n\n```text\n{output_str}\n```")

def _render_error(event: Any) -> str:
    return telegramify_markdown.markdownify(f"🚨 **INTERNAL ERROR**\n\n{event.message}")

def _render_stats(event: Any) -> str:
    model = event.model or "unknown"
    text = (
        f"📊 **STATS**\n"
        f"• Model: `{model}`\n"
        f"• Tokens: `{event.tokens_in} in / {event.tokens_out} out`\n"
        f"• Time: `{event.duration_ms}ms`"
    )
    return telegramify_markdown.markdownify(text)

def _render_done(event: Any) -> str:
    return telegramify_markdown.markdownify(f"🏁 **DONE**\n\nSession: `{event.session_id}`")

# Dictionary of handlers for a more Pythonic dispatch
RENDERERS = {
    "text": _render_text,
    "thought": _render_thought,
    "tool_call": _render_tool_call,
    "tool_result": _render_tool_result,
    "error": _render_error,
    "stats": _render_stats,
    "done": _render_done,
}

def render_event(event: BackendEvent) -> str:
    """Render a backend event using the RENDERERS dispatch dictionary."""
    renderer = RENDERERS.get(event.kind)
    if renderer:
        return renderer(event)
    return ""

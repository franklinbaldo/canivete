import json
import telegramify_markdown
from canivete.bot.backends.base import BackendEvent

def render_event(event: BackendEvent) -> str:
    """Render a backend event into a Telegram-compatible MarkdownV2 string."""
    
    if event.kind == "text":
        # Text from the assistant: clear and direct
        return telegramify_markdown.markdownify(event.text)
    
    if event.kind == "thought":
        # Thoughts: Monospace block or italicized block
        subject = event.subject or "Thinking"
        desc = event.description or ""
        text = f"💭 *{subject}*\n\n{desc}"
        return telegramify_markdown.markdownify(text)

    if event.kind == "tool_call":
        # Tool Calls: Modernist "Action" block
        args_str = json.dumps(event.args, indent=2, ensure_ascii=False)
        text = f"🛠️ **EXECUTE: {event.tool}**\n```json\n{args_str}\n```"
        return telegramify_markdown.markdownify(text)
    
    if event.kind == "tool_result":
        # Tool Results: Success/Failure status
        status = "✅ SUCCESS" if event.ok else "❌ FAILURE"
        output_str = event.output or ""
        # Truncate large outputs for Telegram
        if len(output_str) > 2000:
            output_str = output_str[:1997] + "..."
        
        text = f"📥 **RESULT: {status}**\n\n```text\n{output_str}\n```"
        return telegramify_markdown.markdownify(text)

    if event.kind == "error":
        # Errors: Urgent visual
        text = f"🚨 **INTERNAL ERROR**\n\n{event.message}"
        return telegramify_markdown.markdownify(text)
    
    if event.kind == "stats":
        # Stats: Technical footer style
        model = event.model or "unknown"
        text = (
            f"📊 **STATS**\n"
            f"• Model: `{model}`\n"
            f"• Tokens: `{event.tokens_in} in / {event.tokens_out} out`\n"
            f"• Time: `{event.duration_ms}ms`"
        )
        return telegramify_markdown.markdownify(text)
    
    if event.kind == "done":
        # Finalization
        text = f"🏁 **DONE**\n\nSession: `{event.session_id}`"
        return telegramify_markdown.markdownify(text)
    
    return ""

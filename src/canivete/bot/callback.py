import json
import urllib.parse
import urllib.request
from typing import Any

from canivete.tg import _api_url


def _post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def handle_callback_query(query: dict[str, Any]) -> str | None:
    query_id = query.get("id")
    if not query_id:
        return None

    url_answer = _api_url("answerCallbackQuery")
    _post_json(url_answer, {"callback_query_id": query_id})

    message = query.get("message")
    if message:
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")
        if chat_id and message_id:
            url_edit = _api_url("editMessageReplyMarkup")
            _post_json(
                url_edit,
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": {"inline_keyboard": []},
                },
            )

    data = query.get("data", "")
    from_user = query.get("from", {})
    first_name = from_user.get("first_name", "User")
    msg_id = message.get("message_id", "?") if message else "?"

    button_text = "Unknown"
    if message and "reply_markup" in message:
        for row in message["reply_markup"].get("inline_keyboard", []):
            for btn in row:
                if btn.get("callback_data") == data:
                    button_text = btn.get("text", "Unknown")
                    break

    return (
        f'[{first_name} clicked button "{button_text}" (callback_data: {data}) on message {msg_id}]'
    )

import asyncio
import collections
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Thread

from rich.console import Console

from canivete.bot.backends import REGISTRY
from canivete.bot.backends.base import Backend, SpawnResult
from canivete.bot.callback import handle_callback_query
from canivete.bot.commands import handle_dynamic_command
from canivete.bot.fatal import FATAL_PATTERNS, SUGGESTIONS
from canivete.bot.render import render_event
from canivete.tg import _api_url

err_console = Console(stderr=True)
console = Console()


def build_system_prompt(agent_root: Path) -> str:
    """Concatena os .md ALL-CAPS na raiz do agent_root num único string,
    pulando CLAUDE.md (auto-carregado pelo Claude Code da cwd), GEMINI.md
    (idem pelo gemini-cli), README.md (humano), e SYSTEM.md (gerado).
    Ordem: alfabética por filename."""
    skip = {"CLAUDE.md", "GEMINI.md", "README.md", "SYSTEM.md"}
    chunks = []
    for f in sorted(agent_root.glob("*.md")):
        if f.name in skip:
            continue
        if f.stem != f.stem.upper():
            continue  # não é all-caps
        chunks.append(f"# {f.name}\n\n{f.read_text(encoding='utf-8')}\n\n---\n")
    return "\n".join(chunks)


SYSTEM_PROMPT = build_system_prompt(Path(os.environ.get("AGENT_ROOT", ".")))
if not SYSTEM_PROMPT:
    err_console.print("[yellow]Warning:[/] no manifests found in AGENT_ROOT")


def _post_json(url: str, payload: dict) -> dict | None:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        err_console.print(f"[red]Telegram API Error:[/] {e}")
        return None


def _get_updates(offset: int) -> list[dict]:
    url = _api_url("getUpdates")
    res = _post_json(
        url, {"offset": offset, "timeout": 30, "allowed_updates": ["message", "callback_query"]}
    )
    if res and res.get("ok"):
        return res.get("result", [])
    return []


def send_message(chat_id: int | str, text: str, reply_to: int | None = None) -> int | None:
    url = _api_url("sendMessage")
    payload = {"chat_id": chat_id, "text": text}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    res = _post_json(url, payload)
    if res and res.get("ok"):
        return res["result"]["message_id"]
    return None


def edit_message(chat_id: int | str, message_id: int, text: str) -> None:
    url = _api_url("editMessageText")
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    _post_json(url, payload)


class ChatWorker:
    def __init__(self, chat_id: int, backend_name: str):
        self.chat_id = chat_id
        self.backend_name = backend_name
        self.backend: Backend | None = None
        self.session_id: str | None = None
        self.is_new_session: bool = True
        self.buffer: list[str] = []
        self.is_running = False

        self.stderr_buffer = collections.deque(maxlen=100)
        self.fatal_error_matched: tuple[str, str] | None = None
        self.start_time: float = 0
        self.timeout = int(os.environ.get("AGENT_TIMEOUT", "300"))

    def spawn_backend(self, prompt: str):
        if self.is_running:
            return

        backend_cls = REGISTRY.get(self.backend_name)
        if not backend_cls:
            err_console.print(f"[red]Unknown backend:[/] {self.backend_name}")
            return

        self.backend = backend_cls()
        self.is_running = True
        self.fatal_error_matched = None
        self.stderr_buffer.clear()
        self.start_time = time.time()

        if self.session_id is None:
            self.session_id = self.backend.generate_session_id()

        spawn_res = self.backend.spawn(
            prompt=prompt,
            session_id=self.session_id,
            attachments=[],
            system_prompt=SYSTEM_PROMPT,
            is_new_session=self.is_new_session,
        )

        if hasattr(self.backend, "proc") and self.backend.proc.stderr:
            thread = Thread(
                target=self._watch_stderr, args=(self.backend.proc.stderr,), daemon=True
            )
            thread.start()

        thread_timeout = Thread(target=self._watch_timeout, daemon=True)
        thread_timeout.start()

        asyncio.create_task(self._consume_events(spawn_res))

    def _watch_stderr(self, stderr_pipe):
        for line in iter(stderr_pipe.readline, ""):
            if not line:
                break
            self.stderr_buffer.append(line)

            for pattern, kind, summary in FATAL_PATTERNS:
                if pattern.search(line):
                    self.fatal_error_matched = (kind, summary)
                    if self.backend:
                        self.backend.kill()
                    return

    def _watch_timeout(self):
        while self.is_running:
            if time.time() - self.start_time > self.timeout:
                self.fatal_error_matched = ("timeout", "Subprocess hit AGENT_TIMEOUT.")
                if self.backend:
                    self.backend.kill()
                return
            time.sleep(1)

    async def _consume_events(self, spawn_res: SpawnResult):
        msg_id = send_message(self.chat_id, "⏳ *Starting...*")

        full_text = ""
        last_edit_time = 0.0

        try:
            async for event in spawn_res.events:
                rendered = render_event(event)
                if rendered:
                    full_text += rendered + "\n"

                    now = time.time()
                    if now - last_edit_time > 1.0 and msg_id:
                        await asyncio.to_thread(edit_message, self.chat_id, msg_id, full_text)
                        last_edit_time = now

        except Exception as e:
            err_console.print(f"[red]Error consuming events:[/] {e}")

        finally:
            self.is_running = False
            if spawn_res.session_id:
                self.session_id = spawn_res.session_id
            self.is_new_session = False

            if msg_id:
                await asyncio.to_thread(edit_message, self.chat_id, msg_id, full_text)

            self._handle_fatal_exit()

    def _handle_fatal_exit(self):
        if self.fatal_error_matched:
            kind, summary = self.fatal_error_matched
            suggestion = SUGGESTIONS.get(kind, "")

            exit_code = "?"
            if hasattr(self.backend, "proc") and self.backend.proc.poll() is not None:
                exit_code = self.backend.proc.poll()

            duration = int(time.time() - self.start_time)

            stderr_str = "".join(list(self.stderr_buffer)[-10:])[-800:]

            msg = f"""⚠️ *{summary}*

exit code: {exit_code}
duration: {duration}s

── stderr (last lines) ──
```
{stderr_str}
```

── what to try ──
{suggestion}
"""
            asyncio.create_task(asyncio.to_thread(send_message, self.chat_id, msg))

    def handle_text(self, text: str):
        if text == "/cancel":
            if self.backend:
                self.backend.kill()
            asyncio.create_task(asyncio.to_thread(send_message, self.chat_id, "Cancelled."))
            return
        if text in ("/new", "/reset"):
            old_id = self.session_id
            self.session_id = None
            self.is_new_session = True

            msg = (
                f"✨ Próxima mensagem abre sessão nova.\nAnterior preservada: `{old_id}`."
                if old_id
                else "✨ Próxima mensagem abre primeira sessão deste chat."
            )
            asyncio.create_task(asyncio.to_thread(send_message, self.chat_id, msg))
            return
        if text in ("/status", "/cron", "/config"):
            asyncio.create_task(
                asyncio.to_thread(
                    send_message, self.chat_id, "Command not implemented in meta-harness yet."
                )
            )
            return

        self.spawn_backend(text)


class Daemon:
    def __init__(self, backend_name: str):
        self.backend_name = backend_name
        self.workers: dict[int, ChatWorker] = {}

    def get_worker(self, chat_id: int) -> ChatWorker:
        if chat_id not in self.workers:
            self.workers[chat_id] = ChatWorker(chat_id, self.backend_name)
        return self.workers[chat_id]

    async def run(self):
        offset = 0
        console.print(f"[green]Daemon started[/] with backend: [bold]{self.backend_name}[/]")
        while True:
            updates = await asyncio.to_thread(_get_updates, offset)
            for update in updates:
                offset = update["update_id"] + 1

                if "message" in update:
                    msg = update["message"]
                    chat_id = msg.get("chat", {}).get("id")
                    text = msg.get("text")
                    first_name = msg.get("from", {}).get("first_name", "User")
                    if chat_id and text:
                        pseudo_msg = handle_dynamic_command(text, first_name)
                        if pseudo_msg:
                            self.get_worker(chat_id).handle_text(pseudo_msg)
                        else:
                            self.get_worker(chat_id).handle_text(text)

                elif "callback_query" in update:
                    cb = update["callback_query"]
                    chat_id = cb.get("message", {}).get("chat", {}).get("id")
                    if chat_id:
                        pseudo_msg = await asyncio.to_thread(handle_callback_query, cb)
                        if pseudo_msg:
                            self.get_worker(chat_id).handle_text(pseudo_msg)

            await asyncio.sleep(0.5)


def run_daemon(backend_name: str):
    daemon = Daemon(backend_name)
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        console.print("[yellow]Daemon stopped.[/]")

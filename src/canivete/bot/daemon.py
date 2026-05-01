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
import logging

from rich.console import Console

from canivete.bot.backends import REGISTRY
from canivete.bot.backends.base import Backend, SpawnResult
from canivete.bot.callback import handle_callback_query
from canivete.bot.commands import handle_dynamic_command
from canivete.bot.fatal import FATAL_PATTERNS, SUGGESTIONS
from canivete.bot.media import (
    download_telegram_file,
    is_audio_document,
    mime_to_ext,
    persist_to_inbound,
    transcribe_audio,
)
from canivete.bot.render import render_event
from canivete.tg import _api_url

err_console = Console(stderr=True)
console = Console()
log = logging.getLogger("canivete.bot")

_BASE_CMDS = [
    {"command": "cancel", "description": "aborta a execução atual"},
    {"command": "status", "description": "ocioso"},  # será sobrescrito
    {"command": "reload", "description": "reinicia o daemon (hot-reload)"},
    {"command": "update", "description": "atualiza o canivete e reinicia"},
    {"command": "cron", "description": "lista jobs agendados"},
    {"command": "reset", "description": "zera a sessão atual"},
    {"command": "config", "description": "altera configurações do bot"},
]

# Live state global do bot daemon.
_live_status = {
    "phase": "ocioso",
    "started_at": None,
    "current_tool": None,
    "tools_total": 0,
    "tools_done": 0,
    "texts_sent": 0,
    "thoughts": 0,
    "last_done_s": None,
    "error": None,
}
_live_status_lock = asyncio.Lock()
_status_desc_cache = [None]

# Ícones para ferramentas no status do menu slash.
TOOL_ICONS = {
    "run_shell_command": "💻", "Bash": "💻",
    "read_file": "📖", "Read": "📖",
    "write_file": "✏️", "Write": "✏️", "replace": "✏️", "Edit": "✏️",
    "glob": "🔍", "Glob": "🔍",
    "search_file_content": "🔍", "Grep": "🔍",
    "google_web_search": "🌐", "WebSearch": "🌐",
    "web_fetch": "🌐", "WebFetch": "🌐",
}
DEFAULT_ICON = "⚙️"


def _short_tool(name: str, tool_input: dict | None = None) -> str:
    '''Versão compacta do tool_use pra caber na descrição.'''
    if not name:
        return ""
    icon = TOOL_ICONS.get(name, DEFAULT_ICON)
    ti = tool_input or {}
    detail = ""
    if name in ("read_file", "write_file", "replace", "Read", "Write", "Edit"):
        path = (ti.get("file_path") or ti.get("path") or "").rsplit("/", 1)[-1]
        detail = f"({path[:15]})" if path else ""
    elif name in ("run_shell_command", "Bash"):
        cmd = (ti.get("command") or "").strip().split(" ", 1)[0]
        detail = f"({cmd[:10]})" if cmd else ""
    return f"{icon}{name}{detail}"


def compose_status_desc() -> str:
    s = _live_status
    bits = []
    if s["phase"] == "processando":
        elapsed = int(time.monotonic() - s["started_at"]) if s["started_at"] else 0
        bits.append(f"⚙️ {elapsed}s")
        if s["current_tool"]:
            bits.append(s["current_tool"])
        if s["tools_total"]:
            bits.append(f"{s['tools_done']}/{s['tools_total']}🔧")
        if s["thoughts"]:
            bits.append(f"{s['thoughts']}💭")
        if s["texts_sent"]:
            bits.append(f"{s['texts_sent']}t")
    elif s["phase"] == "pronto":
        head = "✓ pronto"
        if s["last_done_s"] is not None:
            head += f" · {s['last_done_s']:.1f}s"
        bits.append(head)
    elif s["phase"] == "erro":
        bits.append("❌ " + (s["error"] or "erro"))
    else:
        bits.append("ocioso")
        if s["last_done_s"] is not None:
            bits.append(f"última: {s['last_done_s']:.1f}s")

    return " · ".join(bits)[:256]


async def update_live_status(**kwargs):
    async with _live_status_lock:
        if kwargs:
            _live_status.update(kwargs)
        desc = compose_status_desc()

    if _status_desc_cache[0] == desc:
        return
    _status_desc_cache[0] = desc

    cmds = [({**c, "description": desc} if c["command"] == "status" else c) for c in _BASE_CMDS]
    await asyncio.to_thread(_post_json, _api_url("setMyCommands"), {"commands": json.dumps(cmds)})


async def _status_ticker_loop():
    while True:
        await asyncio.sleep(5)
        active = False
        async with _live_status_lock:
            if _live_status["phase"] == "processando":
                active = True
        if active:
            await update_live_status()


_HEADER_RULE = "=" * 64


def build_system_prompt(agent_root: Path) -> str:
    '''Concatena os .md ALL-CAPS na raiz do agent_root num único string.'''
    if not agent_root.exists():
        return ""
    skip = {"CLAUDE.md", "GEMINI.md", "README.md", "SYSTEM.md"}
    candidates = []
    for f in agent_root.glob("*.md"):
        if f.name in skip:
            continue
        if f.stem != f.stem.upper():
            continue
        candidates.append(f)

    soul = next((f for f in candidates if f.name == "SOUL.md"), None)
    rest = sorted((f for f in candidates if f is not soul), key=lambda p: p.name)
    ordered = ([soul] if soul else []) + rest

    chunks = []
    for f in ordered:
        try:
            body = f.read_text(encoding="utf-8")
            chunks.append(f"{_HEADER_RULE}\nFILE: {f}\n{_HEADER_RULE}\n\n{body}\n")
        except Exception:
            continue
    return "\n".join(chunks)


def write_system_prompt(workspace: Path, content: str, backend_name: str):
    '''Escreve o conteúdo concatenado no arquivo que o backend espera (GEMINI.md ou SYSTEM.md).'''
    filename = "SYSTEM.md" if "claude" in backend_name else "GEMINI.md"
    target = workspace / filename
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        console.print(f"[dim]Written context to {target} ({len(content)} chars)[/]")
    except Exception as e:
        err_console.print(f"[red]Failed to write context file:[/] {e}")


async def check_health():
    '''Verificação de saúde básica: DNS e conectividade Telegram.'''
    try:
        import socket
        socket.gethostbyname("api.telegram.org")
        return True, "Healthy"
    except Exception as e:
        return False, f"DNS/Network Failure: {e}"


async def _health_guard_loop():
    '''Monitora a saúde do sistema e tenta se auto-corrigir.'''
    while True:
        await asyncio.sleep(60)
        ok, msg = await check_health()
        if not ok:
            err_console.print(f"[bold red]HEALTH ALERT:[/] {msg}")
        else:
            log.debug("Health check passed.")


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


def _audio_prompt(first_name: str, ts: str, path: Path, transcript: str | None) -> str:
    if transcript:
        return (
            f"[Áudio de {first_name} via Telegram em {ts}, transcrito via Whisper. "
            f"Original: {path}]\n\n{transcript}"
        )
    return (
        f"[Áudio de {first_name} via Telegram em {ts}, salvo em {path}. "
        "Whisper indisponível — não transcrito.]"
    )


def _message_text_from_media(msg: dict, first_name: str) -> str | None:
    """Convert Telegram media updates into the text prompt expected by workers."""
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    caption = msg.get("caption") or ""
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    media = None
    suffix = ".ogg"
    if msg.get("voice"):
        media = msg["voice"]
        suffix = ".ogg"
    elif msg.get("audio"):
        media = msg["audio"]
        suffix = mime_to_ext(media.get("mime_type"), ".mp3")
    elif msg.get("document") and is_audio_document(msg["document"]):
        media = msg["document"]
        name = media.get("file_name") or ""
        suffix = Path(name).suffix or mime_to_ext(media.get("mime_type"), ".bin")

    if not media:
        return caption or None

    tmp = download_telegram_file(media["file_id"], suffix=suffix)
    if not tmp:
        return caption or "[Erro ao baixar áudio do Telegram]"

    path = persist_to_inbound(tmp, suffix)
    try:
        transcript = transcribe_audio(path)
    except Exception as exc:
        err_console.print(f"[red]Audio transcription failed:[/] {exc}")
        transcript = None

    if transcript and chat_id:
        send_message(chat_id, f"🎤 {transcript}", reply_to=message_id)

    prompt = _audio_prompt(first_name, ts, path, transcript)
    return f"{caption}\n\n{prompt}".strip() if caption else prompt


_last_edit_text: dict[tuple[int | str, int], str] = {}


def edit_message(chat_id: int | str, message_id: int, text: str) -> None:
    if not text:
        return
    key = (chat_id, message_id)
    if _last_edit_text.get(key) == text:
        return
    _last_edit_text[key] = text
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

        console.print(f"[cyan]Spawning backend {self.backend_name} for chat {self.chat_id}...[/]")
        self.backend = backend_cls()
        self.is_running = True
        self.fatal_error_matched = None
        self.stderr_buffer.clear()
        self.start_time = time.monotonic()

        agent_root = Path(os.environ.get("AGENT_ROOT", "."))
        workspace = Path(os.environ.get("WORKSPACE", "."))
        system_prompt = build_system_prompt(agent_root)
        write_system_prompt(workspace, system_prompt, self.backend_name)

        asyncio.create_task(
            update_live_status(
                phase="processando",
                started_at=self.start_time,
                current_tool=None,
                tools_total=0,
                tools_done=0,
                thoughts=0,
                texts_sent=0,
                error=None,
            )
        )

        if self.session_id is None:
            self.session_id = self.backend.generate_session_id()

        spawn_res = self.backend.spawn(
            prompt=prompt,
            session_id=self.session_id,
            attachments=[],
            system_prompt=system_prompt,
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
            if time.monotonic() - self.start_time > self.timeout:
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
                status_update = {}
                if event.kind == "text":
                    async with _live_status_lock:
                        _live_status["texts_sent"] += 1
                elif event.kind == "tool_call":
                    status_update["current_tool"] = _short_tool(event.tool, event.args)
                    async with _live_status_lock:
                        _live_status["tools_total"] += 1
                elif event.kind == "tool_result":
                    async with _live_status_lock:
                        _live_status["tools_done"] += 1
                elif event.kind == "thought":
                    async with _live_status_lock:
                        _live_status["thoughts"] += 1

                if status_update or event.kind in ("text", "tool_call", "tool_result", "thought"):
                    await update_live_status(**status_update)

                rendered = render_event(event)
                if rendered:
                    full_text += rendered + "\n"
                    now = time.time()
                    if now - last_edit_time > 1.0 and msg_id:
                        await asyncio.to_thread(edit_message, self.chat_id, msg_id, full_text)
                        last_edit_time = now

        except Exception as e:
            err_console.print(f"[red]Error consuming events:[/] {e}")
            await update_live_status(phase="erro", error=str(e)[:50])

        finally:
            self.is_running = False
            duration = time.monotonic() - self.start_time
            if spawn_res.session_id:
                self.session_id = spawn_res.session_id
            self.is_new_session = False
            if msg_id:
                await asyncio.to_thread(edit_message, self.chat_id, msg_id, full_text)
            async with _live_status_lock:
                if _live_status["phase"] == "processando":
                    await update_live_status(phase="pronto", last_done_s=duration, current_tool=None)
            self._handle_fatal_exit()

    def _handle_fatal_exit(self):
        if self.fatal_error_matched:
            kind, summary = self.fatal_error_matched
            suggestion = SUGGESTIONS.get(kind, "")
            exit_code = "?"
            if hasattr(self.backend, "proc") and self.backend.proc.poll() is not None:
                exit_code = self.backend.proc.poll()
            duration = int(time.monotonic() - self.start_time)
            stderr_str = "".join(list(self.stderr_buffer)[-10:])[-800:]
            asyncio.create_task(update_live_status(phase="erro", last_done_s=duration, error=kind))
            msg = f"⚠️ *{summary}*\n\nexit code: {exit_code}\nduration: {duration}s\n\n── stderr ──\n```\n{stderr_str}\n```\n\n── try ──\n{suggestion}"
            asyncio.create_task(asyncio.to_thread(send_message, self.chat_id, msg))

    def handle_text(self, text: str):
        if text == "/cancel":
            if self.backend:
                self.backend.kill()
            asyncio.create_task(asyncio.to_thread(send_message, self.chat_id, "Cancelled."))
            return
        if text in ("/new", "/reset"):
            self.session_id = None
            self.is_new_session = True
            asyncio.create_task(asyncio.to_thread(send_message, self.chat_id, "✨ Session reset."))
            return
        if text in ("/status", "/cron", "/config", "/reload", "/update"):
            if text == "/status":
                desc = compose_status_desc()
                asyncio.create_task(asyncio.to_thread(send_message, self.chat_id, f"📊 *Status:* {desc}"))
            elif text == "/reload":
                asyncio.create_task(self._do_reload())
            elif text == "/update":
                asyncio.create_task(self._do_update())
            return
        self.spawn_backend(text)

    async def _do_reload(self):
        await asyncio.to_thread(send_message, self.chat_id, "♻️ *Reloading daemon...*")
        await asyncio.sleep(1)
        import sys
        os.execv(sys.executable, [sys.executable] + sys.argv)

    async def _do_update(self):
        await asyncio.to_thread(send_message, self.chat_id, "⏳ *Updating canivete via uv...*")
        cmd = "uv pip install --system --upgrade canivete"
        proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            await asyncio.to_thread(send_message, self.chat_id, "✅ *Update successful. Reloading...*")
            await asyncio.sleep(1)
            import sys
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            err = stderr.decode().strip() or stdout.decode().strip()
            await asyncio.to_thread(send_message, self.chat_id, f"❌ *Update failed:*\n```\n{err[:500]}\n```")


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
        
        # Registra comandos e inicia ticker
        try:
            await update_live_status()
            asyncio.create_task(_status_ticker_loop())
            asyncio.create_task(_health_guard_loop())
        except Exception as e:
            err_console.print(f"[red]Error initializing background tasks:[/] {e}")

        while True:
            try:
                updates = await asyncio.to_thread(_get_updates, offset)
                for update in updates:
                    offset = update["update_id"] + 1
                    if "message" in update:
                        msg = update["message"]
                        chat_id = msg.get("chat", {}).get("id")
                        first_name = msg.get("from", {}).get("first_name", "User")
                        text = msg.get("text") or _message_text_from_media(msg, first_name)
                        if chat_id and text:
                            console.print(f"[blue]Received message from {first_name} ({chat_id}):[/] {text[:50]}")
                            pseudo_msg = handle_dynamic_command(text, first_name)
                            self.get_worker(chat_id).handle_text(pseudo_msg or text)
                    elif "callback_query" in update:
                        cb = update["callback_query"]
                        chat_id = cb.get("message", {}).get("chat", {}).get("id")
                        if chat_id:
                            pseudo_msg = await asyncio.to_thread(handle_callback_query, cb)
                            if pseudo_msg:
                                self.get_worker(chat_id).handle_text(pseudo_msg)
            except Exception as e:
                err_console.print(f"[red]Error in main loop:[/] {e}")
                await asyncio.sleep(5)

            await asyncio.sleep(0.5)


def run_daemon(backend_name: str):
    daemon = Daemon(backend_name)
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        console.print("[yellow]Daemon stopped.[/]")

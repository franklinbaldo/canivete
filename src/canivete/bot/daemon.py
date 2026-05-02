import asyncio
import collections
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Thread

from rich.console import Console

from canivete.bot import config
from canivete.bot.backends import REGISTRY, normalize_backend_name
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
    {"command": "kill", "description": "kill agressivo e limpa fila"},
    {"command": "status", "description": "ocioso"},
    {"command": "backend", "description": "mostra ou troca o harness ativo"},
    {"command": "spawn", "description": "troca de harness e injeta um prompt"},
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
_SHARED_CONTEXT_DIR = ".canivete"
_SHARED_CONTEXT_FILE = "context.md"


def _shared_context_path(workspace: Path) -> Path:
    return workspace / _SHARED_CONTEXT_DIR / _SHARED_CONTEXT_FILE


def _append_shared_context(
    workspace: Path,
    *,
    chat_id: int,
    backend_name: str,
    role: str,
    body: str,
):
    if not body.strip():
        return

    target = _shared_context_path(workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    block = (
        f"## {stamp} · chat {chat_id} · backend {backend_name} · {role}\n\n"
        f"```text\n{body.strip()}\n```\n\n"
    )
    with target.open("a", encoding="utf-8") as fh:
        fh.write(block)


def build_system_prompt(agent_root: Path, workspace: Path | None = None) -> str:
    '''Concatena os .md ALL-CAPS na raiz do agent_root num único string.'''
    if not agent_root.exists():
        base = ""
    else:
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
            body = f.read_text(encoding="utf-8")
            chunks.append(f"{_HEADER_RULE}\nFILE: {f}\n{_HEADER_RULE}\n\n{body}\n")
        base = "\n".join(chunks)

    if not workspace:
        return base

    shared = _shared_context_path(workspace)
    if not shared.exists():
        return base

    body = shared.read_text(encoding="utf-8")

    if not body.strip():
        return base

    return "\n".join(
        part
        for part in (
            base,
            f"{_HEADER_RULE}\nFILE: {shared}\n{_HEADER_RULE}\n\n{body}\n",
        )
        if part
    )


def write_system_prompt(workspace: Path, content: str, backend_name: str):
    '''Escreve o conteúdo concatenado no arquivo que o backend espera (GEMINI.md ou SYSTEM.md).'''
    filename = "SYSTEM.md" if "claude" in backend_name else "GEMINI.md"
    target = workspace / filename
    workspace.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    console.print(f"[dim]Written context to {target} ({len(content)} chars)[/]")


async def check_health():
    '''Verificação de saúde básica: DNS e conectividade Telegram.'''
    import socket
    socket.gethostbyname("api.telegram.org")
    return True, "Healthy"


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
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


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
        f"[Áudio de {first_name} via Telegram em {ts}, saved to {path}. "
        "Whisper unavailable — not transcribed.]"
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
    transcript = transcribe_audio(path)

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
        self.backend_name = normalize_backend_name(backend_name)
        self.backend: Backend | None = None
        self.session_id: str | None = None
        self.last_session_id: str | None = None
        self.is_new_session: bool = True
        self.backend_sessions: dict[str, str | None] = {self.backend_name: None}
        self.pending_new_session: dict[str, bool] = {self.backend_name: False}
        self.buffer: list[str] = []
        self.is_running = False

        self.stderr_buffer = collections.deque(maxlen=100)
        self.fatal_error_matched: tuple[str, str] | None = None
        self.start_time: float = 0
        self.timeout = config.AGENT_TIMEOUT

    def _select_backend(self, backend_name: str) -> bool:
        backend_name = normalize_backend_name(backend_name)
        backend_cls = REGISTRY.get(backend_name)
        if not backend_cls:
            err_console.print(f"[red]Unknown backend:[/] {backend_name}")
            return False

        if backend_name == self.backend_name:
            return True

        self.last_session_id = self.session_id
        if self.session_id is not None:
            self.backend_sessions[self.backend_name] = self.session_id
        self.backend_name = backend_name
        self.backend_sessions.setdefault(self.backend_name, None)
        self.pending_new_session.setdefault(self.backend_name, False)
        if self.pending_new_session[self.backend_name]:
            self.session_id = None
            self.is_new_session = True
        else:
            self.session_id = self.backend_sessions[self.backend_name]
            self.is_new_session = self.session_id is None
        self.backend = None
        return True

    def _state_summary(self) -> str:
        active = self.session_id or "none"
        last = self.last_session_id or "none"
        known = ", ".join(
            f"{name}:{sid or '-'}" for name, sid in sorted(self.backend_sessions.items())
        )
        return f"backend={self.backend_name} · session={active} · last={last} · known={known}"

    def spawn_backend(self, prompt: str, backend_name: str | None = None, mid: int | None = None):
        if self.is_running:
            return

        if backend_name and not self._select_backend(backend_name):
            return

        backend_cls = REGISTRY.get(self.backend_name)
        if not backend_cls:
            err_console.print(f"[red]Unknown backend:[/] {self.backend_name}")
            return

        console.print(f"[cyan]Spawning backend {self.backend_name} for chat {self.chat_id}...[/]")
        
        asyncio.create_task(self._run_with_fallbacks(prompt, backend_cls, mid=mid))

    async def _run_with_fallbacks(self, prompt: str, backend_cls: type[Backend], mid: int | None = None):
        tried = set()
        started_at_total = time.monotonic()
        
        while True:
            model = config.get_next_available_model(skip=tried)
            if model:
                tried.add(model)
            
            self.backend = backend_cls()
            self.is_running = True
            self.fatal_error_matched = None
            self.stderr_buffer.clear()
            self.start_time = time.monotonic()

            agent_root = Path(config.AGENT_ROOT)
            workspace = Path(config.WORKSPACE)
            _append_shared_context(
                workspace,
                chat_id=self.chat_id,
                backend_name=self.backend_name,
                role="user",
                body=prompt,
            )
            system_prompt = build_system_prompt(agent_root, workspace)
            write_system_prompt(workspace, system_prompt, self.backend_name)

            await update_live_status(
                phase="processando",
                started_at=self.start_time,
                current_tool=None,
                tools_total=0,
                tools_done=0,
                thoughts=0,
                texts_sent=0,
                error=None,
            )

            if self.session_id is None:
                self.session_id = self.backend.generate_session_id()

            # Pass model override to spawn if backend supports it
            spawn_kwargs = {
                "prompt": prompt,
                "session_id": self.session_id,
                "attachments": [],
                "system_prompt": system_prompt,
                "is_new_session": self.is_new_session,
            }
            if model:
                spawn_kwargs["model"] = model

            spawn_res = self.backend.spawn(**spawn_kwargs)

            if hasattr(self.backend, "proc") and self.backend.proc.stderr:
                Thread(target=self._watch_stderr, args=(self.backend.proc.stderr,), daemon=True).start()

            # Timeout check handled within _consume_events for asyncio consistency
            await self._consume_events(spawn_res, mid=mid)
            
            # Check for 429 retry
            if self.fatal_error_matched and self.fatal_error_matched[0] == "429":
                config.mark_model_cooldown(model)
                nxt = config.get_next_available_model(skip=tried)
                if nxt:
                    await asyncio.to_thread(send_message, self.chat_id, f"⏳ Rate limit in {model or 'default'}. Switching to {nxt}...")
                    continue
            
            break # Success or non-retryable error

    def _watch_stderr(self, stderr_pipe):
        for line in iter(stderr_pipe.readline, ""):
            if not line: break
            self.stderr_buffer.append(line)
            for pattern, kind, summary in FATAL_PATTERNS:
                if pattern.search(line):
                    self.fatal_error_matched = (kind, summary)
                    if self.backend:
                        self.backend.kill()
                    return

    async def _consume_events(self, spawn_res: SpawnResult, mid: int | None = None):
        # Initial message
        msg_id = None
        if config.BOT_MODE == "streaming":
            msg_id = await asyncio.to_thread(send_message, self.chat_id, "⏳ *Starting...*", reply_to=mid)

        full_text = ""
        last_edit_time = 0.0
        text_buffer = ""
        first_text = True

        async def flush_buffer():
            nonlocal text_buffer, first_text, msg_id
            chunk = text_buffer.strip()
            text_buffer = ""
            if not chunk: return
            if config.BOT_MODE == "burst":
                reply = mid if first_text else None
                msg_id = await asyncio.to_thread(send_message, self.chat_id, chunk, reply_to=reply)
                first_text = False
            else:
                # Streaming mode: full_text logic is outside flush_buffer
                pass

        try:
            deadline = self.start_time + self.timeout
            async for event in spawn_res.events:
                if time.monotonic() > deadline:
                    self.fatal_error_matched = ("timeout", "Agent timed out.")
                    if self.backend:
                        self.backend.kill()
                    break

                status_update = {}
                if event.kind == "text":
                    if config.BOT_MODE == "burst":
                        text_buffer += event.text
                        # Flush on double newline for responsiveness
                        if "\n\n" in text_buffer:
                            await flush_buffer()
                    else:
                        full_text += event.text
                        now = time.time()
                        if now - last_edit_time > 1.0 and msg_id:
                            await asyncio.to_thread(edit_message, self.chat_id, msg_id, full_text)
                            last_edit_time = now
                    
                    async with _live_status_lock:
                        _live_status["texts_sent"] += 1
                
                elif event.kind == "tool_call":
                    await flush_buffer()
                    status_update["current_tool"] = _short_tool(event.tool, event.args)
                    async with _live_status_lock:
                        _live_status["tools_total"] += 1
                    if config.BOT_MODE == "burst":
                        await asyncio.to_thread(send_message, self.chat_id, f"🔧 `{event.tool}`")
                
                elif event.kind == "tool_result":
                    async with _live_status_lock:
                        _live_status["tools_done"] += 1
                
                elif event.kind == "thought":
                    async with _live_status_lock:
                        _live_status["thoughts"] += 1

                if status_update or event.kind in ("text", "tool_call", "tool_result", "thought"):
                    await update_live_status(**status_update)

            await flush_buffer()

        finally:
            self.is_running = False
            duration = time.monotonic() - self.start_time
            if spawn_res.session_id:
                self.session_id = spawn_res.session_id
            self.backend_sessions[self.backend_name] = self.session_id
            self.pending_new_session[self.backend_name] = False
            self.is_new_session = False
            
            if config.BOT_MODE == "streaming" and msg_id:
                await asyncio.to_thread(edit_message, self.chat_id, msg_id, full_text or "🏁 Done.")
            
            workspace = Path(config.WORKSPACE)
            _append_shared_context(
                workspace,
                chat_id=self.chat_id,
                backend_name=self.backend_name,
                role="assistant",
                body=full_text or text_buffer or "[no renderable output]",
            )
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

    def handle_text(self, text: str, mid: int | None = None):
        if text in ("/cancel", "/kill", "/flush"):
            if self.backend:
                self.backend.kill()
            asyncio.create_task(asyncio.to_thread(send_message, self.chat_id, f"Stop signal sent ({text})."))
            return
        if text.startswith("/backend") or text.startswith("/harness"):
            parts = text.split(maxsplit=1)
            if len(parts) == 1:
                asyncio.create_task(
                    asyncio.to_thread(
                        send_message,
                        self.chat_id,
                        f"📦 {self._state_summary()}",
                    )
                )
                return

            backend_name = parts[1].strip()
            if self._select_backend(backend_name):
                asyncio.create_task(
                    asyncio.to_thread(
                        send_message,
                        self.chat_id,
                        f"✅ Harness ativo: `{self.backend_name}`. Próxima mensagem já usa esse backend.",
                    )
                )
            else:
                asyncio.create_task(
                    asyncio.to_thread(
                        send_message,
                        self.chat_id,
                        f"❌ Harness desconhecido: `{backend_name}`.",
                    )
                )
            return
        if text.startswith("/spawn") or text.startswith("/fork"):
            parts = text.split(maxsplit=2)
            if len(parts) < 2:
                asyncio.create_task(
                    asyncio.to_thread(
                        send_message,
                        self.chat_id,
                        "Uso: `/spawn <backend> [prompt]`",
                    )
                )
                return
            backend_name = parts[1].strip()
            prompt = parts[2].strip() if len(parts) > 2 else ""
            if not self._select_backend(backend_name):
                asyncio.create_task(
                    asyncio.to_thread(
                        send_message,
                        self.chat_id,
                        f"❌ Harness desconhecido: `{backend_name}`.",
                    )
                )
                return
            if not prompt:
                asyncio.create_task(
                    asyncio.to_thread(
                        send_message,
                        self.chat_id,
                        f"✅ Spawn preparado em `{self.backend_name}`. Envie o próximo prompt ou use `/spawn {self.backend_name} ...`.",
                    )
                )
                return
            self.spawn_backend(prompt, mid=mid)
            return
        if text in ("/new", "/reset"):
            self.last_session_id = self.session_id
            self.session_id = None
            self.is_new_session = True
            self.pending_new_session[self.backend_name] = True
            asyncio.create_task(
                asyncio.to_thread(
                    send_message,
                    self.chat_id,
                    f"✨ Session reset. Anterior preservada: `{self.last_session_id or 'none'}`. Backend preservado: `{self.backend_name}`.",
                )
            )
            return
        if text in ("/status", "/cron", "/config", "/reload", "/update"):
            if text == "/status":
                desc = compose_status_desc()
                asyncio.create_task(
                    asyncio.to_thread(
                        send_message,
                        self.chat_id,
                        f"📊 *Status:* {desc}\n\n{self._state_summary()}",
                    )
                )
            elif text == "/reload":
                asyncio.create_task(self._do_reload())
            elif text == "/update":
                asyncio.create_task(self._do_update())
            return
        self.spawn_backend(text, mid=mid)

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
        await update_live_status()
        asyncio.create_task(_status_ticker_loop())
        asyncio.create_task(_health_guard_loop())

        while True:
            updates = await asyncio.to_thread(_get_updates, offset)
            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update:
                    msg = update["message"]
                    chat_id = msg.get("chat", {}).get("id")
                    first_name = msg.get("from", {}).get("first_name", "User")
                    text = msg.get("text") or _message_text_from_media(msg, first_name)
                    if chat_id and text:
                        # Auth check
                        if config.ALLOWED_USERS and str(chat_id) not in config.ALLOWED_USERS:
                            console.print(f"[red]Unauthorized user attempt:[/] {first_name} ({chat_id})")
                            continue
                        
                        mid = msg.get("message_id")
                        console.print(f"[blue]Received message from {first_name} ({chat_id}):[/] {text[:50]}")
                        pseudo_msg = handle_dynamic_command(text, first_name)
                        self.get_worker(chat_id).handle_text(pseudo_msg or text, mid=mid)
                elif "callback_query" in update:
                    cb = update["callback_query"]
                    chat_id = cb.get("message", {}).get("chat", {}).get("id")
                    if chat_id:
                        if config.ALLOWED_USERS and str(chat_id) not in config.ALLOWED_USERS:
                            continue
                        mid = cb.get("message", {}).get("message_id")
                        pseudo_msg = await asyncio.to_thread(handle_callback_query, cb)
                        if pseudo_msg:
                            self.get_worker(chat_id).handle_text(pseudo_msg, mid=mid)

            await asyncio.sleep(0.5)


def run_daemon(backend_name: str):
    daemon = Daemon(backend_name)
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        console.print("[yellow]Daemon stopped.[/]")

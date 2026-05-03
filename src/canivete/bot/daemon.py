import asyncio
import collections
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from rich.console import Console

from canivete import cron
from canivete.bot import media
from canivete.bot.backends import REGISTRY
from canivete.bot.backends.base import Backend, SpawnResult
from canivete.bot.callback import handle_callback_query
from canivete.bot.commands import handle_dynamic_command
from canivete.bot.fatal import FATAL_PATTERNS, SUGGESTIONS
from canivete.bot.render import render_event
from canivete.tg import _api_url

err_console = Console(stderr=True)
console = Console()


_HEADER_RULE = "=" * 64


def build_system_prompt(agent_root: Path) -> str:
    """Concatena os .md ALL-CAPS na raiz do agent_root num único string."""
    skip = {"CLAUDE.md", "GEMINI.md", "README.md", "SYSTEM.md"}
    candidates = []
    for f in agent_root.glob("*.md"):
        if f.name in skip:
            continue
        if f.stem != f.stem.upper():
            continue  # not ALL-CAPS — operational notes, not a manifest
        candidates.append(f)

    soul = next((f for f in candidates if f.name == "SOUL.md"), None)
    rest = sorted((f for f in candidates if f is not soul), key=lambda p: p.name)
    ordered = ([soul] if soul else []) + rest

    chunks = []
    for f in ordered:
        try:
            body = f.read_text(encoding="utf-8")
            chunks.append(f"{_HEADER_RULE}\nFILE: {f}\n{_HEADER_RULE}\n\n{body}\n")
        except Exception as e:
            err_console.print(f"[red]Error reading manifest {f}:[/] {e}")
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
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
        err_console.print(f"[red]Telegram API Error (PID {os.getpid()}):[/] {e}")
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
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "MarkdownV2"}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    res = _post_json(url, payload)
    if not res:
        payload.pop("parse_mode", None)
        res = _post_json(url, payload)
    if res and res.get("ok"):
        return res["result"]["message_id"]
    return None


def set_message_reaction(chat_id: int | str, message_id: int, emoji: str | None) -> None:
    url = _api_url("setMessageReaction")
    reaction = [{"type": "emoji", "emoji": emoji}] if emoji else []
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reaction": reaction,
    }
    _post_json(url, payload)


# Fix Memory Leak: Use OrderedDict with limit
_last_edit_text: collections.OrderedDict[tuple[int | str, int], str] = collections.OrderedDict()
_MAX_EDIT_CACHE = 1000

def _cache_last_edit(key: tuple[int | str, int], text: str):
    if key in _last_edit_text:
        _last_edit_text.move_to_end(key)
    _last_edit_text[key] = text
    if len(_last_edit_text) > _MAX_EDIT_CACHE:
        _last_edit_text.popitem(last=False)

def edit_message(chat_id: int | str, message_id: int, text: str) -> None:
    if not text:
        return
    key = (chat_id, message_id)
    if _last_edit_text.get(key) == text:
        return
    
    url = _api_url("editMessageText")
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "MarkdownV2"}

    res = _post_json(url, payload)
    if res and res.get("ok"):
        _cache_last_edit(key, text)
    elif not res:
        payload.pop("parse_mode", None)
        res = _post_json(url, payload)
        if res and res.get("ok"):
            _cache_last_edit(key, text)


class ChatWorker:
    def __init__(self, chat_id: int, backend_name: str):
        self.chat_id = chat_id
        self.backend_name = backend_name
        self.backend: Backend | None = None
        self.session_id: str | None = None
        self.is_new_session: bool = True
        self.queue: collections.deque[tuple[str, int | None]] = collections.deque()
        self.is_running = False

        self.stderr_buffer = collections.deque(maxlen=100)
        self.fatal_error_matched: tuple[str, str] | None = None
        self.start_time: float = 0
        self.last_activity: float = 0
        self.timeout = int(os.environ.get("AGENT_TIMEOUT", "300"))
        self.inactivity_timeout = int(os.environ.get("AGENT_INACTIVITY_TIMEOUT", "60"))
        
        self.watchdog_task: asyncio.Task | None = None
        self.stderr_task: asyncio.Task | None = None

    def handle_text(self, text: str, origin_message_id: int | None = None):
        if text == "/cancel":
            if self.backend:
                self.backend.kill()
            self.queue.clear()
            asyncio.create_task(asyncio.to_thread(send_message, self.chat_id, "⛔ *Cancelled.* Fila limpa."))
            return
        
        if text in ("/new", "/reset"):
            old_id = self.session_id
            self.session_id = None
            self.is_new_session = True
            msg = (
                f"✨ Próxima mensagem abre sessão nova.\nAnterior preservada: `{old_id}`."
                if old_id else "✨ Próxima mensagem abre primeira sessão deste chat."
            )
            asyncio.create_task(asyncio.to_thread(send_message, self.chat_id, msg))
            return

        self.queue.append((text, origin_message_id))
        if not self.is_running:
            self.process_queue()

    async def handle_message_dict(self, msg: dict):
        """Processes a raw Telegram message dict, handling media and text."""
        chat_id = msg.get("chat", {}).get("id")
        message_id = msg.get("message_id")
        first_name = msg.get("from", {}).get("first_name", "User")
        
        text = msg.get("text")
        photo = msg.get("photo")
        voice = msg.get("voice")
        audio = msg.get("audio")
        document = msg.get("document")

        prompt = ""
        if text:
            pseudo = handle_dynamic_command(text, first_name)
            prompt = pseudo or text
        
        attachment_info = []
        
        try:
            if photo:
                # Take highest res
                file_id = photo[-1]["file_id"]
                path = await asyncio.to_thread(media.download_telegram_file, file_id)
                attachment_info.append(f"[Photo: {path.name}]")
            
            if voice or audio:
                file_id = (voice or audio)["file_id"]
                path = await asyncio.to_thread(media.download_telegram_file, file_id)
                transcript = await asyncio.to_thread(media.transcribe_audio, path)
                attachment_info.append(f"[Audio: {path.name} | Transcript: {transcript}]")
            
            if document:
                file_id = document["file_id"]
                path = await asyncio.to_thread(media.download_telegram_file, file_id)
                attachment_info.append(f"[File: {path.name}]")
        except Exception as e:
            err_console.print(f"[red]Media error:[/] {e}")
            attachment_info.append(f"[Media processing failed: {e}]")

        if attachment_info:
            final_prompt = "\n".join(attachment_info)
            if prompt:
                final_prompt += f"\n\nUser message: {prompt}"
            prompt = final_prompt

        if prompt:
            if message_id:
                await asyncio.to_thread(set_message_reaction, self.chat_id, message_id, "👀")
            self.handle_text(prompt, origin_message_id=message_id)

    def process_queue(self):
        if self.is_running or not self.queue:
            return

        prompt, mid = self.queue.popleft()
        asyncio.create_task(self.spawn_backend(prompt, origin_message_id=mid))

    async def spawn_backend(self, prompt: str, origin_message_id: int | None = None):
        backend_cls = REGISTRY.get(self.backend_name)
        if not backend_cls:
            err_console.print(f"[red]Unknown backend:[/] {self.backend_name}")
            return

        self.backend = backend_cls()
        self.is_running = True
        self.fatal_error_matched = None
        self.stderr_buffer.clear()
        self.start_time = time.time()
        self.last_activity = self.start_time

        if self.session_id is None:
            self.session_id = self.backend.generate_session_id()

        spawn_res = await asyncio.to_thread(
            self.backend.spawn,
            prompt=prompt,
            session_id=self.session_id,
            attachments=[],
            system_prompt=SYSTEM_PROMPT,
            is_new_session=self.is_new_session,
        )

        if hasattr(self.backend, "proc") and self.backend.proc.stderr:
            self.stderr_task = asyncio.create_task(
                asyncio.to_thread(self._watch_stderr, self.backend.proc.stderr)
            )

        self.watchdog_task = asyncio.create_task(self._watch_watchdog())
        await self._consume_events(spawn_res, origin_message_id)

    def _watch_stderr(self, stderr_pipe):
        for line in iter(stderr_pipe.readline, ""):
            if not line: break
            self.stderr_buffer.append(line)
            for pattern, kind, summary in FATAL_PATTERNS:
                if pattern.search(line):
                    self.fatal_error_matched = (kind, summary)
                    if self.backend: self.backend.kill()
                    return

    async def _watch_watchdog(self):
        """Monitor de travamento e timeout global."""
        while self.is_running:
            now = time.time()
            if now - self.start_time > self.timeout:
                self.fatal_error_matched = ("timeout", f"Execution exceeded {self.timeout}s.")
                if self.backend: self.backend.kill()
                return
            if now - self.last_activity > self.inactivity_timeout:
                self.fatal_error_matched = ("stuck", f"No activity for {self.inactivity_timeout}s.")
                if self.backend: self.backend.kill()
                return
            await asyncio.sleep(2)

    async def _consume_events(self, spawn_res: SpawnResult, origin_message_id: int | None = None):
        msg_id = await asyncio.to_thread(send_message, self.chat_id, "⏳ *Starting...*")
        full_text = ""
        last_edit_time = 0.0
        last_sent_text = ""

        try:
            async for event in spawn_res.events:
                self.last_activity = time.time()
                if event.kind == "done" and event.session_id:
                    self.session_id = event.session_id
                
                rendered = render_event(event)
                if rendered:
                    full_text += rendered + "\n"
                    now = time.time()
                    if now - last_edit_time > 1.2 and msg_id and full_text != last_sent_text:
                        await asyncio.to_thread(edit_message, self.chat_id, msg_id, full_text)
                        last_sent_text = full_text
                        last_edit_time = now
        except Exception as e:
            err_console.print(f"[red]Error consuming events:[/] {e}")
        finally:
            self.is_running = False
            if self.watchdog_task: self.watchdog_task.cancel()
            if self.stderr_task: self.stderr_task.cancel()
            
            if spawn_res.session_id: self.session_id = spawn_res.session_id
            self.is_new_session = False

            if msg_id and full_text and full_text != last_sent_text:
                await asyncio.to_thread(edit_message, self.chat_id, msg_id, full_text)
            elif msg_id and not full_text and not self.fatal_error_matched:
                await asyncio.to_thread(edit_message, self.chat_id, msg_id, "❌ Backend silent exit.")

            if origin_message_id:
                emoji = "⚠️" if self.fatal_error_matched else "✅"
                await asyncio.to_thread(set_message_reaction, self.chat_id, origin_message_id, emoji)

            self._handle_fatal_exit()
            self.process_queue()

    def _handle_fatal_exit(self):
        if not self.fatal_error_matched: return
        kind, summary = self.fatal_error_matched
        suggestion = SUGGESTIONS.get(kind, "Tente novamente ou use /reset.")
        exit_code = "?"
        if hasattr(self.backend, "proc") and self.backend.proc.poll() is not None:
            exit_code = self.backend.proc.poll()
        
        duration = int(time.time() - self.start_time)
        stderr_str = "".join(list(self.stderr_buffer)[-10:])[-800:]
        msg = f"⚠️ *{summary}*\n\nexit: {exit_code} | {duration}s\n\n── stderr ──\n```\n{stderr_str}\n```\n\n{suggestion}"
        asyncio.create_task(asyncio.to_thread(send_message, self.chat_id, msg))


class Daemon:
    def __init__(self, backend_name: str):
        self.backend_name = backend_name
        self.workers: dict[int, ChatWorker] = {}

    def get_worker(self, chat_id: int) -> ChatWorker:
        if chat_id not in self.workers:
            self.workers[chat_id] = ChatWorker(chat_id, self.backend_name)
        return self.workers[chat_id]

    def _reap_zombies(self):
        # Only reap if we have children and use WNOHANG to not block.
        # Avoid -1 to be safe against stealing exit codes if possible, 
        # but for now we keep it simple since backends manage their own procs usually.
        try:
            while True:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0: break
        except ChildProcessError: pass

    async def _poll_cron(self):
        """Background task to poll for due cron jobs."""
        while True:
            try:
                due = await asyncio.to_thread(cron.check_due_jobs)
                for job in due:
                    chat_id = os.environ.get("CRON_CHAT_ID")
                    if chat_id:
                        console.print(f"[yellow]Firing cron job {job['id']} for {chat_id}[/]")
                        worker = self.get_worker(int(chat_id))
                        worker.handle_text(job["prompt"])
                        await asyncio.to_thread(cron.mark_job_fired, job["id"])
            except Exception as e:
                err_console.print(f"[red]Cron poll error:[/] {e}")
            await asyncio.sleep(10)

    async def run(self):
        offset = 0
        console.print(f"[green]Daemon started[/] with backend: [bold]{self.backend_name}[/]")
        
        # Start cron poller
        asyncio.create_task(self._poll_cron())
        
        while True:
            await asyncio.to_thread(self._reap_zombies)
            updates = await asyncio.to_thread(_get_updates, offset)
            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update:
                    msg = update["message"]
                    chat_id = msg.get("chat", {}).get("id")
                    if chat_id:
                        asyncio.create_task(self.get_worker(chat_id).handle_message_dict(msg))
                elif "callback_query" in update:
                    cb = update["callback_query"]
                    chat_id = cb.get("message", {}).get("chat", {}).get("id")
                    if chat_id:
                        pseudo = await asyncio.to_thread(handle_callback_query, cb)
                        if pseudo:
                            self.get_worker(chat_id).handle_text(pseudo, origin_message_id=cb.get("message", {}).get("message_id"))
            await asyncio.sleep(0.5)

def run_daemon(backend_name: str):
    daemon = Daemon(backend_name)
    try: asyncio.run(daemon.run())
    except KeyboardInterrupt: console.print("[yellow]Stopped.[/]")

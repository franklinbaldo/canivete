# Plano: `canivete bot` — meta-harness genérico

**Status:** v2 (2026-04-28 — `BackendEvent` virou discriminated union;
unificou A→D num PR só)
**Escopo:** transformar `canivete` em meta-harness — um único daemon
versionado e testado que substitui os `bot.py` duplicados em
`harnesses/gemini/` e `harnesses/claude-code/` do repo `ireneo-funes`.
**Estimativa:** um PR único cobrindo daemon + 2 backends + consumer +
fail-fast. Um dia de Jules.
**Dependentes:** futura padronização de qualquer agente Funes novo;
consumer side dos PRs `tg buttons` e `tg commands` (que hoje só emitem).

---

## Problema

Hoje há **dois `bot.py` quase idênticos** mantidos em paralelo:

- `harnesses/gemini/bot.py` — Ireneo + Aparicio (backend `gemini-cli`).
- `harnesses/claude-code/bot.py` — Claudio (backend `claude` Anthropic).

Os dois compartilham ~95% do código: polling do Telegram, pipeline de
mídia (`media/inbound/`, transcrição via Whisper, 🎤 echo), slash
commands (`/cancel`, `/status`, `/cron`, `/reset`, `/config`), config
dinâmica via Pydantic, tool icons no streaming render, etc. A única
diferença real é a chamada `subprocess.Popen` final ao backend CLI.

Consequências:

| Sintoma | Causa |
|---|---|
| Drift entre os dois arquivos a cada mudança | Edição duplicada manual |
| Sem cobertura de testes | Nenhum dos dois `bot.py` tem testes BDD |
| `tg buttons` e `tg commands` (PRs #4 e tg-commands) sem consumer | Receber `callback_query` e dispatch de slash dinâmico foi escopo *bot.py*, mas ficou no limbo |
| Adicionar agente novo (Hermes, Codex, Aider) requer fork | Não há ponto único de extensão |

---

## Solução: `canivete bot`

Um subcomando novo no `canivete` que **é** o daemon de Telegram para
qualquer backend. Cada agente Funes roda:

```
canivete bot --backend gemini-cli      # Ireneo, Aparicio
canivete bot --backend claude-code     # Claudio
```

…em vez de `python /app/bot.py`. A idiossincrasia de cada agente sai
do **código** e passa pra **dados/config**:

- `SOUL.md` / `TOOLS.md` / arquivos do `workspace/`.
- Env vars (`AGENT_BACKEND`, `TELEGRAM_BOT_TOKEN`, `CRON_CHAT_ID`, etc.).
- `/workspace/.config.json` (Pydantic).

### Estrutura proposta

```
src/canivete/
  bot/
    __init__.py
    cli.py              # @app.command("bot") in canivete.cli
    daemon.py           # main polling loop, chat workers, slash dispatch
    media.py            # download_telegram_file, persist_to_inbound, transcribe_audio
    render.py           # streaming markdown render (move from bot.py atual)
    backends/
      __init__.py       # registry + Backend protocol
      gemini_cli.py     # adapter for gemini-cli
      claude_code.py    # adapter for claude
      base.py           # protocol: spawn(prompt, session_id, attachments) -> AsyncIterator[Event]
    callback.py         # callback_query handling (consumer for `tg buttons`)
    commands.py         # dynamic slash dispatch (consumer for `tg commands`)
```

### Backend protocol

`BackendEvent` é uma **discriminated union** sobre `kind`, não um
`payload: dict` solto. Isso elimina o `payload.get('foo', {})` em
toda parte e dá refactor seguro quando o stream-json de um backend
mudar de formato.

```python
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, Field


class TextEvent(BaseModel):
    kind: Literal["text"] = "text"
    text: str


class ToolCallEvent(BaseModel):
    kind: Literal["tool_call"] = "tool_call"
    tool: str                    # ex.: "read_file" / "Read"
    args: dict                   # opacidade limitada ao adapter
    call_id: str | None = None


class ToolResultEvent(BaseModel):
    kind: Literal["tool_result"] = "tool_result"
    call_id: str | None = None
    ok: bool
    output: str | None = None    # texto resumido; payloads grandes ficam fora


class ThoughtEvent(BaseModel):
    kind: Literal["thought"] = "thought"
    subject: str | None = None
    description: str | None = None


class ErrorEvent(BaseModel):
    kind: Literal["error"] = "error"
    message: str
    fatal: bool = False          # quando True, daemon aborta a sessão


class StatsEvent(BaseModel):
    kind: Literal["stats"] = "stats"
    duration_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cached: int | None = None
    model: str | None = None


class DoneEvent(BaseModel):
    kind: Literal["done"] = "done"
    session_id: str | None = None  # backend pode informar a session que persistiu


BackendEvent = Annotated[
    TextEvent | ToolCallEvent | ToolResultEvent | ThoughtEvent
    | ErrorEvent | StatsEvent | DoneEvent,
    Field(discriminator="kind"),
]


class SpawnResult(BaseModel):
    """Retorno do `spawn` — separa o stream de eventos da metadata
    final (session_id que o backend persistiu, exit code, etc.).

    O daemon consome `events` em loop e, quando o iterator termina,
    lê os campos pós-stream pra fechar contas (cron, manifest, log)."""
    events: AsyncIterator[BackendEvent]   # consumível 1×
    session_id: str | None = None         # preenchido após `events` esgotar
    exit_code: int | None = None


class Backend(Protocol):
    """Adapter contract for a coding-agent CLI."""
    name: str

    def spawn(
        self,
        prompt: str,
        *,
        session_id: str | None,
        attachments: list[Path],
    ) -> SpawnResult: ...

    def kill(self) -> None: ...


REGISTRY: dict[str, type[Backend]] = {
    "gemini-cli": GeminiCliBackend,
    "claude-code": ClaudeCodeBackend,
}
```

Cada adapter encapsula:
- Como invocar a CLI (`gemini ...` vs `claude ...`).
- Como parsear o `stream-json` em `BackendEvent` tipado (não dict cru).
- Como popular `SpawnResult.session_id` quando o backend criar/recuperar
  uma sessão (gemini-cli grava em `~/.gemini/tmp/<workspace>/chats/...`;
  claude-code idem em `~/.claude/projects/...`).
- Como encerrar (graceful + force kill).

### Packaging

Como o daemon traz dependências que o `tg`/`cron` standalone não
precisam (`jinja2`, `telegramify-markdown`, `pytest-bdd` em testes),
usar **extra opt-in**:

```toml
[project.optional-dependencies]
bot = ["jinja2>=3.1", "telegramify-markdown>=0.5", "pydantic-settings>=2.0"]
```

`pip install canivete` traz só `tg`/`cron`/`profile`/etc.
`pip install 'canivete[bot]'` traz o daemon. Os Dockerfiles dos
harnesses passam a usar a variante `[bot]`.

---

## Escopo do PR

**Um único PR** entrega A→D combinados (decisão pragmática — o Jules
aguenta o pacote inteiro de uma vez, e fatiar geraria churn de
sequenciamento sem ganho real de revisão):

1. **Daemon + protocol + GeminiCliBackend** — porta o que hoje é
   `bot.py`-comum pra `canivete/bot/daemon.py`. Define `Backend`
   protocol tipado, `BackendEvent` como discriminated union, registry.
2. **ClaudeCodeBackend** — adapter completo pro `claude` Anthropic.
   Mesmos eventos, mesma cobertura BDD do gemini.
3. **callback_query + dynamic slash dispatch consumer** — o lado
   receiver dos PRs `tg buttons` e `tg commands` (já merged). Quando
   user clica botão, daemon chama `answerCallbackQuery` e injeta
   pseudo-message no buffer. Quando user invoca slash dinâmico, idem.
4. **Fail-fast em 429 + erro melhorado no Telegram** — detecção de
   `RESOURCE_EXHAUSTED` / `monthly spending cap` / etc. no stderr
   live; kill imediato do subprocess; mensagem rica no Telegram com
   tipo do erro, trecho do stderr, sugestão acionável. Hard timeout
   absoluto via env var (`AGENT_TIMEOUT`, default 300s).

Após esse PR mergiar, os Dockerfiles dos 3 harnesses migram pra
`canivete bot --backend X`, e os `bot.py` legacy ficam como shims
vazios (ou são removidos). Migração final = PR no `ireneo-funes`,
fora do escopo deste plano.

---

## O que NÃO muda

- Interface dos slash commands no Telegram (`/cancel`, `/status`,
  `/cron`, `/reset`, `/config`) — usuário não nota.
- Formato dos transcripts JSONL persistidos.
- Estrutura do `workspace/` de cada agente.
- O package canivete sem `[bot]` extra continua leve.

---

## Riscos

| Risco | Mitigação |
|---|---|
| Streaming `stream-json` parsing diverge entre backends | Backend protocol tipado (`BackendEvent`), tests comparativos PR B |
| Migração quebra Ireneo/Aparicio em produção | Os 3 PRs são adições; Dockerfile dos harnesses só muda no PR final, fora deste plano |
| `[bot]` extra exige `pip install` adicional nos harnesses | Documentado no Dockerfile + nota no README |
| Adapter Claude difere demais (ex.: claude-code não tem `stream-json` igual) | Tests no PR B vão expor mismatches; Backend protocol absorve diferenças |

---

## Critério de conclusão

Após PR D:

- `canivete bot --backend gemini-cli` substitui `python /app/bot.py`
  no harness gemini sem regressão funcional.
- `canivete bot --backend claude-code` substitui idem no harness claude.
- Adicionar um 4º backend (ex.: `aider`, `cursor`) requer **só** um
  novo módulo em `canivete/bot/backends/<name>.py` implementando o
  Backend protocol, e linha no registry. Nenhuma edição em `daemon.py`.
- O consumer side dos `tg buttons` e `tg commands` está vivo —
  callback_query e slash dinâmico viram pseudo-mensagens no buffer
  do agente.
- Cobertura BDD substancial sobre o daemon (que hoje não tem testes).

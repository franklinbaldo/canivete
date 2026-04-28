# Plano: `canivete bot` — meta-harness genérico

**Status:** v1
**Escopo:** transformar `canivete` em meta-harness — um único daemon
versionado e testado que substitui os `bot.py` duplicados em
`harnesses/gemini/` e `harnesses/claude-code/` do repo `ireneo-funes`.
**Estimativa:** 4 PRs sequenciais, 1–2 semanas.
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

```python
from typing import Protocol, AsyncIterator

class BackendEvent(BaseModel):
    kind: Literal["text", "tool_call", "tool_result", "thought", "error", "done"]
    payload: dict

class Backend(Protocol):
    name: str
    def spawn(self, prompt: str, *, session_id: str | None,
              attachments: list[Path]) -> AsyncIterator[BackendEvent]: ...
    def kill(self) -> None: ...

REGISTRY: dict[str, type[Backend]] = {
    "gemini-cli": GeminiCliBackend,
    "claude-code": ClaudeCodeBackend,
}
```

Cada adapter encapsula:
- Como invocar a CLI (`gemini ...` vs `claude ...`).
- Como parsear o `stream-json` do CLI em `BackendEvent`.
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

## Sequência de PRs

```
PR A — bot/daemon.py + Backend protocol + GeminiCliBackend adapter
  - Move o que hoje é bot.py-comum para canivete/bot/daemon.py
  - Define Backend protocol e registry
  - Implementa GeminiCliBackend
  - Mantém ClaudeCodeBackend como TODO comentado, fora de escopo deste PR
  - Tests BDD que cobrem o ciclo polling → spawn → render → exit, com
    backend mockado (não invoca gemini real)

PR B — ClaudeCodeBackend
  - Adapter completo pra claude
  - Tests BDD comparativos com GeminiCliBackend (mesmas scenarios, backends diferentes)
  - Diff entre bot.py legacy e canivete/bot pra Claude documentado em PR description

PR C — callback_query consumer + dynamic slash dispatch consumer
  - Implementa o lado receiver dos PRs `tg buttons` e `tg commands`
  - Quando user clica botão, daemon chama answerCallbackQuery e injeta
    pseudo-message ([User clicked X]) no buffer do agente
  - Quando user invoca slash dinâmico, daemon despacha pro agente
  - Tests cobrem o round-trip emitir → render → click → consume

PR D — fail-fast + erro melhorado (movido de ireneo-funes)
  - Migra o trabalho da sessão Jules `6804685494745469640` (rate_limit
    detection, hard timeout, Telegram error message) pro daemon novo
  - Aproveita o Backend protocol pra detectar fatal patterns no stderr
    de qualquer backend, não só gemini
```

Após PR D, os Dockerfiles dos 3 harnesses migram pra `canivete bot`,
e os `bot.py` legacy ficam como shims vazios (ou são removidos).
Migração final = PR no `ireneo-funes`, fora do escopo deste plano.

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

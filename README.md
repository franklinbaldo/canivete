# 🇨🇭🔪 canivete

> **Swiss-army CLI for AI agents on Telegram.**
> One command, many blades — the toolkit your bot uses to talk back.

`canivete` is a single CLI that bundles the small utilities an AI agent
needs when it lives inside a Telegram bot harness. Send messages and
files, schedule prompts for itself — all under a unified
`canivete <command>` interface, with rich help and a consistent look.

It was extracted from the [Funes
project](https://github.com/franklinbaldo/ireneo-funes) — three Telegram
agents (Ireneo, Aparicio, Claudio) that share the same toolkit despite
running on different model backends (Gemini CLI, Claude Code).

## Quickstart

With [`uv`](https://docs.astral.sh/uv/) (recommended):

```bash
uv pip install 'canivete @ git+https://github.com/franklinbaldo/canivete@main'
```

Or with plain pip:

```bash
pip install 'canivete @ git+https://github.com/franklinbaldo/canivete@main'
```

Set the env vars your bot already uses:

```bash
export TELEGRAM_BOT_TOKEN=123:ABC...
export CRON_CHAT_ID=8490688452       # default destination
```

Then:

```bash
canivete                              # show the overview banner
canivete tg text "hello"              # send a text message
canivete tg photo /path/img.png       # send an image
canivete tg document /path/file.pdf   # send any file
canivete cron add --in 30m "remind me to check the build"
canivete cron list                    # see pending jobs
```

Every subcommand has a `--help`:

```bash
canivete tg --help
canivete cron add --help
```

## Commands

### 📨 `canivete tg`

Send things to Telegram. Subcommands map 1-to-1 to Bot API methods:

| Subcommand | Telegram method | Notes |
|---|---|---|
| `text` | `sendMessage` | plain text |
| `photo` | `sendPhoto` | jpg/png/webp, supports `--caption` |
| `document` | `sendDocument` | pdf/zip/any file |
| `voice` | `sendVoice` | ogg/opus, no caption |
| `video` | `sendVideo` | mp4 |
| `audio` | `sendAudio` | mp3/m4a (not voice) |

All accept `--chat-id` (override default) and `--reply-to`.

### ⏰ `canivete cron`

Schedule prompts that come back to the agent later, as if the user had
typed them. Storage is a JSONL append-only log at `/workspace/.cron.jsonl`
(override with `CRON_LOG`); the bot daemon polls it.

```bash
canivete cron add --in 30m "check whisper logs"
canivete cron add --at "2026-12-31T23:59:00-03:00" "year recap"
canivete cron list
canivete cron rm j_a1b2c3d4
```

The point isn't to run a job — it's to **wake the agent up later with a
prompt** so it can act in a future turn. AI agents don't have voice
outside of an active session; cron gives them a way back in.

## Why "canivete"?

It's the Brazilian word for *Swiss Army knife*. The repo is open-sourced
in the spirit that a small kit of well-named, well-documented tools
beats one mega-script every time — especially when the user is a
language model reading `--help`.

## Status

Alpha. API and command names may shift before `1.0`. Pin to a tag if
you depend on it in production.

## Development

```bash
git clone https://github.com/franklinbaldo/canivete && cd canivete
uv pip install -e ".[dev]"
ruff format .
ruff check .
pytest                          # runs pytest-bdd scenarios
```

Tests are written in **BDD** style (Gherkin features in `tests/features/`,
step definitions in `tests/step_defs/`). New behaviour starts with a
scenario; the step definitions follow.

## License

[MIT](LICENSE)

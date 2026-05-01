Feature: Bot Daemon and Adapters

  Scenario: canivete bot --help lists the daemon's options
    When I run "canivete bot --help"
    Then the exit code is 0
    And the output contains "Backend to use"

  Scenario: Unknown backend exits non-zero with a clear error
    When I run the bot daemon with backend "unknown-backend"
    Then the daemon prints "Unknown backend: unknown-backend"
    # Actually our daemon loops until killed, but in a test we can mock getUpdates to return a message that triggers the backend spawn and assert the error message.

  Scenario: Daemon spawns the backend with the correct prompt
    When a user sends "Hello agent"
    Then the bot daemon should spawn the "gemini-cli" backend with prompt "Hello agent"

  Scenario: Streaming text and done events produce editMessageText calls
    Given the backend emits "text" and "done" events
    When a user sends a message
    Then the daemon calls editMessageText with the rendered events

  Scenario: Backend that emits no renderable events shows a fallback message
    Given the backend emits no renderable events
    When a user sends a message
    Then the daemon edits the placeholder with a "Backend exited without producing any output" fallback

  Scenario: Telegram socket timeout does not crash the daemon
    Given urlopen raises TimeoutError
    When the daemon polls Telegram
    Then _post_json returns None and logs the error

  Scenario: Fatal pattern in stderr triggers kill and posts error
    Given the backend stderr emits "RESOURCE_EXHAUSTED"
    When a user sends a message
    Then the daemon immediately kills the subprocess
    And the daemon posts an error message with suggestion for "rate_limit"

  Scenario: Hard timeout fires when the backend doesn't exit
    Given the backend process hangs for "AGENT_TIMEOUT"
    When a user sends a message
    Then the daemon kills the subprocess
    And the daemon posts a timeout error message

  Scenario: callback_query with data="vote_yes" results in pseudo-message
    When a user clicks an inline button with data "vote_yes"
    Then the daemon calls answerCallbackQuery
    And the daemon injects a pseudo-message containing "vote_yes" into the chat worker

  Scenario: Dynamic slash command produces a pseudo-message
    When a user sends the dynamic command "/pick_2"
    Then the daemon injects a pseudo-message containing "invoked /pick_2" into the chat worker
    When a user sends the static command "/cancel"
    Then the daemon does not inject a pseudo-message

  Scenario: ClaudeBackend gera UUIDv7 ao criar sessão nova
    When the daemon spawns a Claude backend for a new chat
    Then it passes --session-id with a valid UUIDv7

  Scenario: ClaudeBackend usa --resume em invocação subsequente
    Given a chat has an active session_id "0190d5f1-4c00-7f38-b7d8-1a4c6c8e3a2d"
    When the daemon spawns Claude for the same chat
    Then it passes --resume 0190d5f1-4c00-7f38-b7d8-1a4c6c8e3a2d

  Scenario: GeminiBackend retorna None ao gerar session_id
    When the daemon asks Gemini backend for a new session_id
    Then it returns None

  Scenario: /new command preserva session anterior
    Given a chat has session_id S1
    When user sends /new
    Then worker.session_id is None
    And worker.is_new_session is True
    And the message confirms "Anterior preservada: S1"

  Scenario: Mockable smoke assert both work
    Given we simulate both "gemini-cli" and "claude-code"
    Then both backends should handle basic message flow

  Scenario: build_system_prompt concatenates all-caps md files
    Given an agent root with SOUL.md, TOOLS.md, CLAUDE.md, and README.md
    When I build the system prompt
    Then it returns a string with SOUL.md and TOOLS.md concatenated
    And each file is prefixed with a FILE: <fullpath> header, SOUL.md first

  Scenario: build_system_prompt skips CLAUDE.md, GEMINI.md, README.md, and SYSTEM.md
    Given an agent root with SOUL.md, CLAUDE.md, GEMINI.md, README.md, and SYSTEM.md
    When I build the system prompt
    Then it returns a string with SOUL.md only
    And it does not contain CLAUDE.md, GEMINI.md, README.md, or SYSTEM.md

  Scenario: build_system_prompt ignores non-all-caps files
    Given an agent root with SOUL.md and notes.md
    When I build the system prompt
    Then it returns a string with SOUL.md only
    And it does not contain notes.md

  Scenario: build_system_prompt returns empty string if no valid manifests exist
    Given an agent root with no all-caps md files
    When I build the system prompt
    Then it returns an empty string

  Scenario: ClaudeCodeBackend passes system prompt via CLI flag
    When I spawn ClaudeCodeBackend with a system prompt "I am Claudio"
    Then the claude command includes "--append-system-prompt" and "I am Claudio"

  Scenario: GeminiCliBackend writes GEMINI.md into WORKSPACE
    When I spawn GeminiCliBackend with a system prompt "I am Aparicio"
    Then it writes "I am Aparicio" to GEMINI.md in the workspace

  Scenario: KiloBackend writes AGENTS.md into WORKSPACE
    When I spawn KiloBackend with a system prompt "I am Ireneo"
    Then it writes "I am Ireneo" to AGENTS.md in the workspace

  Scenario: KiloBackend command uses --auto + --format json + positional prompt
    When I spawn KiloBackend with prompt "Hello"
    Then the kilo command includes "run", "--auto", "--format", "json"
    And the kilo command ends with positional prompt "Hello"

  Scenario: KiloBackend retorna None ao gerar session_id
    When the daemon asks Kilo backend for a new session_id
    Then it returns None

  Scenario: CursorBackend writes CURSOR.md into WORKSPACE
    When I spawn CursorBackend with a system prompt "I am Cursor"
    Then it writes "I am Cursor" to CURSOR.md in the workspace

  Scenario: CursorBackend command uses cursor-agent -p --output-format stream-json --force
    When I spawn CursorBackend with prompt "Hello"
    Then the cursor command includes "cursor-agent", "-p", "Hello", "--output-format", "stream-json", "--force"

  Scenario: CursorBackend retorna None ao gerar session_id
    When the daemon asks Cursor backend for a new session_id
    Then it returns None

  Scenario: ClineBackend writes .clinerules into WORKSPACE
    When I spawn ClineBackend with a system prompt "I am Cline"
    Then it writes "I am Cline" to .clinerules in the workspace

  Scenario: ClineBackend command uses cline -y
    When I spawn ClineBackend with prompt "Hello"
    Then the cline command includes "cline", "-y", "Hello"

  Scenario: ClineBackend retorna None ao gerar session_id
    When the daemon asks Cline backend for a new session_id
    Then it returns None

  Scenario: OpenCodeBackend writes OPENCODE.md into WORKSPACE
    When I spawn OpenCodeBackend with a system prompt "I am OpenCode"
    Then it writes "I am OpenCode" to OPENCODE.md in the workspace

  Scenario: OpenCodeBackend command uses opencode run
    When I spawn OpenCodeBackend with prompt "Hello"
    Then the opencode command includes "opencode", "run", "Hello"

  Scenario: OpenCodeBackend retorna None ao gerar session_id
    When the daemon asks OpenCode backend for a new session_id
    Then it returns None

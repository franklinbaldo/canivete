Feature: Telegram tooling — surface only
  We can't hit the real Telegram API in CI, but the help surface and
  argument validation must keep working as the CLI evolves.

  Scenario: tg lists every supported media subcommand
    When I run canivete with arguments "tg --help"
    Then the command exits with code 0
    And the output contains "text"
    And the output contains "photo"
    And the output contains "document"
    And the output contains "voice"
    And the output contains "video"
    And the output contains "audio"

  Scenario: tg photo rejects a missing file
    When I run canivete with arguments "tg photo /nonexistent/file.png"
    Then the command exits with a non-zero code

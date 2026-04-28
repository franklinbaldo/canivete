Feature: Spin up a Telegram Web App from raw HTML
  As an AI agent on Telegram
  I want to send raw HTML and spin up a Telegram Web App
  So that the user can interact with my generated UI

  Background:
    Given TELEGRAM_BOT_TOKEN is set
    And CRON_CHAT_ID is set

  Scenario: Sending with --html-file invokes gh gist and POSTs to Telegram
    Given a temporary HTML file exists
    When I run canivete with arguments "miniapp send 'Pick a date' --html-file <html_file>"
    Then the command exits with code 0
    And the output contains "✓"
    And the GitHub CLI was called to create a gist
    And a Telegram message was sent with a web_app button

  Scenario: Mutual exclusion of arguments
    Given a temporary HTML file exists
    When I run canivete with arguments "miniapp send 'Hello' --html '<h1>hi</h1>' --html-file <html_file>"
    Then the command exits with code 2
    And the output contains "exactly one of"

  Scenario: --gist-id skips gist creation entirely
    When I run canivete with arguments "miniapp send 'Reuse' --gist-id abcd1234"
    Then the command exits with code 0
    And the output contains "✓"
    And the GitHub CLI was NOT called
    And a Telegram message was sent with a web_app button for gist "abcd1234"

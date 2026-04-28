Feature: Spin up a Telegram Web App from raw HTML
  As an AI agent on Telegram
  I want to send raw HTML and spin up a Telegram Web App
  So that the user can interact with my generated UI

  Background:
    Given TELEGRAM_BOT_TOKEN is set
    And CRON_CHAT_ID is set

  Scenario: Small HTML defaults to inline base64 path
    Given a temporary HTML file exists
    When I run canivete with arguments "miniapp send 'Pick a date' --html-file <html_file>"
    Then the command exits with code 0
    And the output contains "✓"
    And the GitHub CLI was NOT called
    And a Telegram message was sent with a web_app button using inline base64
    And the output contains "inline (?b64=)"

  Scenario: Large HTML falls back to gist
    Given a temporary large HTML file exists
    When I run canivete with arguments "miniapp send 'Big UI' --html-file <large_html_file>"
    Then the command exits with code 0
    And the output contains "✓"
    And the GitHub CLI was called to create a gist
    And a Telegram message was sent with a web_app button
    And the output contains "gist"

  Scenario: --inline with large HTML fails
    Given a temporary large HTML file exists
    When I run canivete with arguments "miniapp send 'Big UI' --html-file <large_html_file> --inline"
    Then the command exits with a non-zero code
    And the GitHub CLI was NOT called
    And the output contains "HTML too large for inline"

  Scenario: --gist with small HTML forces gist
    Given a temporary HTML file exists
    When I run canivete with arguments "miniapp send 'Small UI' --html-file <html_file> --gist"
    Then the command exits with code 0
    And the output contains "✓"
    And the GitHub CLI was called to create a gist
    And a Telegram message was sent with a web_app button
    And the output contains "gist"

  Scenario: UTF-8 characters roundtrip correctly
    Given a temporary UTF-8 HTML file exists
    When I run canivete with arguments "miniapp send 'UTF8 UI' --html-file <utf8_html_file>"
    Then the command exits with code 0
    And the output contains "✓"
    And the GitHub CLI was NOT called
    And a Telegram message was sent with a web_app button using inline base64
    And the payload is successfully decoded and matches UTF-8 string

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

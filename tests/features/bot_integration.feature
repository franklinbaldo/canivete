Feature: bot daemon end-to-end
  Scenario: Telegram message → Claude backend → response
    Given a mock Telegram API
    And a mock claude subprocess returning text events
    When the daemon receives a message from chat 123
    Then the daemon spawns claude with the message as prompt
    And the daemon sends the response text back via Telegram

  Scenario: /new resets session
    Given a chat with active session_id "abc-123"
    When user sends /new
    Then worker.session_id is None
    And the response message confirms

  Scenario: fail-fast on 429
    Given a mock claude subprocess emitting "429 Too Many Requests" on stderr
    When the daemon spawns it
    Then the daemon kills it within 1 second
    And the daemon sends a fatal error message to Telegram

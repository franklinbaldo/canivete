Feature: tg buttons
  Send a message with an inline keyboard

  Scenario: canivete tg buttons --help
    When I run canivete with arguments "tg buttons --help"
    Then the command exits with code 0
    And the output contains "--row"
    And the output contains "--json"
    And the output contains "--json-file"

  Scenario: Building a payload from --row produces an inline_keyboard
    When I run tg buttons with "tg buttons 'Question' --row 'A:a B:b' --chat-id 123"
    Then the command exits with code 0
    And the telegram api is called with text "Question" and inline_keyboard "A:a", "B:b"

  Scenario: Building from --json works equivalently
    When I run tg buttons with "tg buttons --json '{\"text\": \"Question\", \"rows\": [[{\"label\": \"A\", \"data\": \"a\"}, {\"label\": \"B\", \"data\": \"b\"}]]}' --chat-id 123"
    Then the command exits with code 0
    And the telegram api is called with text "Question" and inline_keyboard "A:a", "B:b"

  Scenario: Mutually-exclusive flags exit non-zero
    When I run canivete with arguments "tg buttons 'Question' --json '{\"text\": \"Hi\"}'"
    Then the command exits with a non-zero code
    And the output contains "mutually-exclusive"

  Scenario: A callback_data longer than 64 bytes exits non-zero
    When I run canivete with arguments "tg buttons 'Question' --row 'A:this_is_a_very_long_callback_data_that_exceeds_sixty_four_bytes_in_length_and_should_fail'"
    Then the command exits with a non-zero code
    And the output contains "exceeds 64 bytes"

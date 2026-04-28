Feature: Profile self-configuration
  The bot needs to be able to set its own name, descriptions and profile picture.

  Scenario: profile lists all supported subcommands
    When I run canivete with arguments "profile --help"
    Then the command exits with code 0
    And the output contains "photo"
    And the output contains "name"
    And the output contains "description"
    And the output contains "short"
    And the output contains "show"

  Scenario: profile photo rejects a missing file
    When I run canivete with arguments "profile photo /nonexistent/file.png"
    Then the command exits with a non-zero code

  Scenario: profile name is reflected by profile show
    When I mock the Telegram API
    And I run canivete with arguments "profile name 'Claudio Funes'"
    Then the command exits with code 0
    When I run canivete with arguments "profile show"
    Then the command exits with code 0
    And the output contains "Claudio Funes"

Feature: tg commands
  Manage chat-scoped slash commands

  Scenario: Set chat-scoped commands
    Given I mock urllib
    When I run `canivete tg commands set "pick_1:option one" "pick_2:option two"`
    Then it exits with 0
    And urllib was called with "setMyCommands"
    And the urlopen request data has scope type "chat" and chat_id "123"
    And the urlopen request data has commands with "pick_1" and "pick_2"

  Scenario: Invalid command name
    Given I mock urllib
    When I run `canivete tg commands set "Bad-Command:fails"`
    Then it exits with 1
    And stderr contains "Invalid command"

  Scenario: Clear chat-scoped commands
    Given I mock urllib
    When I run `canivete tg commands clear`
    Then it exits with 0
    And urllib was called with "deleteMyCommands"
    And the urlopen request data has scope type "chat" and chat_id "123"

  Scenario: List chat-scoped commands
    Given I mock urllib with predefined response for list commands
    When I run `canivete tg commands list`
    Then it exits with 0
    And urllib was called with "getMyCommands"
    And the urlopen request data has scope type "chat" and chat_id "123"
    And stdout contains "pick_1"

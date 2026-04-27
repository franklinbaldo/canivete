Feature: Overview banner
  As a new user opening the CLI
  I want to see what canivete can do at a glance
  So that I know which subcommand to reach for

  Scenario: Bare invocation shows the tree banner
    When I run canivete with no arguments
    Then the command exits with code 0
    And the output contains "canivete"
    And the output contains "tg"
    And the output contains "cron"

  Scenario: --version prints the version string
    When I run canivete with arguments "--version"
    Then the command exits with code 0
    And the output contains "canivete"

  Scenario Outline: Each subcommand exposes --help
    When I run canivete with arguments "<subcommand> --help"
    Then the command exits with code 0
    And the output contains "<keyword>"

    Examples:
      | subcommand | keyword |
      | tg         | photo   |
      | tg         | document|
      | cron       | add     |
      | cron       | list    |

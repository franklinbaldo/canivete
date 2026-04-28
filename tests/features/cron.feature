Feature: Schedule prompts for later
  As an AI agent on Telegram
  I want to schedule prompts for myself to fire later
  So that I can act in a future turn without staying alive

  Background:
    Given the cron log is empty

  Scenario: Listing an empty log
    When I run canivete with arguments "cron list"
    Then the command exits with code 0
    And the output contains "no pending jobs"

  Scenario: Adding a relative job
    When I schedule a job in 1h with prompt "check the build"
    Then the command exits with code 0
    And the output contains "✓"

  Scenario: Added job appears on list
    Given a job is scheduled in 1h with prompt "remind me later"
    When I run canivete with arguments "cron list"
    Then the command exits with code 0
    And the output contains "remind me later"

  Scenario: Add requires either --at or --in
    When I run canivete with arguments "cron add anything"
    Then the command exits with code 2

  Scenario: Removing a job by id
    Given a job is scheduled in 1h with prompt "to be removed"
    When I remove the most recently added job
    Then the command exits with code 0
    And listing no longer shows "to be removed"

  Scenario: Log path falls back to HOME when neither CRON_LOG nor WORKSPACE is set
    Given no CRON_LOG, WORKSPACE, or XDG_DATA_HOME is set
    When I import the cron module
    Then the resolved log path is under HOME/.local/share/canivete

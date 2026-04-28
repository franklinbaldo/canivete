Feature: Jules CLI
  As an AI agent running inside a bot harness
  I want to interact with Jules (list/create sessions, list sources)
  So that I don't have to resort to raw HTTP requests and fail

  Scenario: Missing JULES_API_KEY env var
    Given I clear the environment
    When I run "canivete jules sessions list"
    Then the command should fail
    And the output should contain "JULES_API_KEY is not set"

  Scenario: Create a new session with basic args
    Given I set the environment variable "JULES_API_KEY" to "fake-key"
    And I mock the Jules API to return a new session
    When I run "canivete jules sessions new 'Fix bug' --source canivete --prompt 'hello'"
    Then the command should succeed
    And the API request body should contain "sources/github/franklinbaldo/canivete"
    And the API request body should specify startingBranch "main"
    And the output should contain "Session created:"

  Scenario: Create a new session with branch override
    Given I set the environment variable "JULES_API_KEY" to "fake-key"
    And I mock the Jules API to return a new session
    When I run "canivete jules sessions new 'Fix bug' --source canivete --prompt 'hello' --branch feature/x"
    Then the command should succeed
    And the API request body should specify startingBranch "feature/x"

  Scenario: Handling API error
    Given I set the environment variable "JULES_API_KEY" to "fake-key"
    And I mock the Jules API to return 400 Bad Request
    When I run "canivete jules sessions new 'Broken' --source fake"
    Then the command should fail
    And the output should contain "Jules API Error"

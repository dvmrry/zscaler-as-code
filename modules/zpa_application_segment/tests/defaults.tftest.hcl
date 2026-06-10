# GENERATED smoke test — plan against a mocked provider; no credentials.
mock_provider "zpa" {}

run "defaults_plan" {
  command = plan

  assert {
    condition     = length(var.items) == 1
    error_message = "sample fixture must contain exactly one item"
  }
}

# GENERATED smoke test — the root composes and plans against a
# mocked provider; no credentials. Regenerate: make gen-env TENANT=zs2
mock_provider "zia" {}

run "empty_plan" {
  command = plan

  variables {
    items = {}
  }
}

# GENERATED smoke test — the root composes and plans against a
# mocked provider; no credentials. Regenerate: make gen-env TENANT=zs2
mock_provider "zcc" {}

run "empty_plan" {
  command = plan

  variables {
    items = {}
  }
}

# GENERATED smoke test — the root composes and plans against a
# mocked provider; no credentials. Regenerate: make gen-env TENANT=zs3
mock_provider "zcc" {}

run "empty_plan" {
  command = plan

  variables {
    items = {}
  }
}

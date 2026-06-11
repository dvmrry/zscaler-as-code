# GENERATED smoke test — the root composes and plans against a
# mocked provider; no credentials. Regenerate: make gen-env TENANT=demo
mock_provider "zcc" {}

run "empty_plan" {
  command = plan

  variables {
    items = {}
  }
}

run "config_plan" {
  command = plan

  variables {
    items = jsondecode(file("../../../config/demo/zcc_failopen_policy.auto.tfvars.json")).items
  }
}

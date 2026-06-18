# GENERATED smoke test — the root composes and plans against a
# mocked provider; no credentials. Regenerate: make gen-env TENANT=demo
mock_provider "zia" {}

run "empty_plan" {
  command = plan

  variables {
    items = {}
  }
}

run "config_plan" {
  command = plan

  variables {
    items = jsondecode(file("../../../config/demo/zia_bandwidth_control_rule.auto.tfvars.json")).items
  }
}

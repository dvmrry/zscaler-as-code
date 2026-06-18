# GENERATED smoke test — the root composes and plans against a
# mocked provider; no credentials. Regenerate: make gen-env TENANT=demo
mock_provider "zpa" {}

run "empty_plan" {
  command = plan

  variables {
    items = {}
  }
}

run "config_plan" {
  command = plan

  variables {
    items = jsondecode(file("../../../config/demo/zpa_microtenant_controller.auto.tfvars.json")).items
  }
}

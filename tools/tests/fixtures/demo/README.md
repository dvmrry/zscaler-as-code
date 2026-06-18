# Demo Dataset — Provenance

These JSON files are input fixtures for `tools/tests/test_demo_pipeline.py`, the
end-to-end pipeline test that exercises the full transform pipeline against
realistic API response shapes.

## Source

Data copied verbatim from Zscaler's public
[zscaler-sdk-python](https://github.com/zscaler/zscaler-sdk-python) integration-test
VCR cassettes (`tests/integration/{zia,zpa}/cassettes/*.yaml`), retrieved
2026-06-10, MIT licence.

The values are Zscaler's own test-sanitized recordings — no real tenant data.
IDs, names, and cross-references are consistent within the cassettes (e.g.
`tests-appsegment-vcr0001`, customer id `216196257331281920`).

## Per-file provenance

| file | cassette | endpoint matched |
|---|---|---|
| `zpa_segment_group.json` | `zpa/cassettes/TestSegmentGroup.yaml` | `/segmentGroup` |
| `zpa_server_group.json` | `zpa/cassettes/TestServerGroup.yaml` | `/serverGroup` |
| `zpa_application_segment.json` | `zpa/cassettes/TestApplicationSegment.yaml` | `/application` |
| `zia_url_categories.json` | `zia/cassettes/TestURLCategories.yaml` | `/urlCategories` (customOnly items) |
| `zia_location_management.json` | `zia/cassettes/TestLocationManagement.yaml` | `/locations` |
| `zia_bandwidth_control_rule.json` | synthetic — shaped from provider schema and DAV-25 clean-room drop report | `/bandwidthControlRules` |
| `zia_dlp_web_rules.json` | synthetic — shaped from provider schema and DAV-25 clean-room drop report | `/webDlpRules` |
| `zia_ssl_inspection_rules.json` | `zia/cassettes/TestSSLInspectionRules.yaml` | `/sslInspectionRules` |
| `zia_cloud_app_control_rule.json` | `zia/cassettes/TestCloudAppControl.yaml` | `/webApplicationRules/STREAMING_MEDIA` |
| `zia_url_filtering_rules.json` | `zia/cassettes/TestURLFilteringRule.yaml` | `/urlFilteringRules` |
| `zia_rule_labels.json` | `zia/cassettes/TestRuleLabels.yaml` | `/ruleLabels` |
| `zpa_app_connector_group.json` | `zpa/cassettes/TestAppConnectorGroup.yaml` | `/appConnectorGroup` |
| `zpa_application_server.json` | `zpa/cassettes/TestApplicationServer.yaml` | `/server` |
| `zpa_microtenant_controller.json` | synthetic — shaped from provider schema and DAV-25 clean-room drop report | `/microtenants` |
| `zpa_policy_access_rule.json` | `zpa/cassettes/TestAccessPolicyRule.yaml` + `TestAccessPolicyRuleV2.yaml` | `policySet/rules/policyType/ACCESS_POLICY` |
| `zcc_forwarding_profile.json` | synthetic — shaped from ZCC provider schema | `zcc/papi/public/v1/webForwardingProfile/listByCompany` |
| `zcc_trusted_network.json` | synthetic — shaped from ZCC provider schema | `zcc/papi/public/v2/trusted-networks` |
| `zcc_failopen_policy.json` | synthetic — shaped from ZCC provider schema | `zcc/papi/public/v1/webFailOpenPolicy/listByCompany` |
| `zcc_web_privacy.json` | synthetic — shaped from ZCC provider schema | `zcc/papi/public/v1/getWebPrivacyInfo` |

## Regeneration

```bash
# Fetch cassettes (requires gh CLI + read access to zscaler/zscaler-sdk-python)
mkdir -p /tmp/cassettes
for f in TestSegmentGroup TestServerGroup TestApplicationSegment; do
  gh api repos/zscaler/zscaler-sdk-python/contents/tests/integration/zpa/cassettes/${f}.yaml \
    --jq .content | base64 -d > /tmp/cassettes/zpa_${f}.yaml
done
for f in TestURLCategories TestLocationManagement TestSSLInspectionRules TestCloudAppControl; do
  gh api repos/zscaler/zscaler-sdk-python/contents/tests/integration/zia/cassettes/${f}.yaml \
    --jq .content | base64 -d > /tmp/cassettes/zia_${f}.yaml
done

# Run extraction (throwaway script, not committed)
uv run --with pyyaml python /tmp/extract.py
```

The extraction script deduplicates by id (last occurrence = most complete
post-update state), keeps only custom URL categories (`customCategory: true`),
caps each resource at 5 items, and writes pretty-printed sorted-key JSON.

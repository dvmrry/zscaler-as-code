"""Offline tests for the quirk miner's pattern battery (tools/mine.py).

Synthetic Go source in BOTH provider dialects — SDKv2 (zia/zpa) and the
terraform plugin framework (zcc) — pins every scanner class so a regex
regression fails here instead of silently dropping a quirk class from
the live run. No network: scan_resource and report are pure functions
over text.
"""
import io
import json
import os
import shutil
import tempfile
import unittest

from tools.mine import find_merge_flatten_helpers, nearest_field, report, scan_resource

SDKV2_SOURCE = '''
package zia

func resourceThing() *schema.Resource {
	return &schema.Resource{
		Schema: map[string]*schema.Schema{
			"size_quota": {
				Type:         schema.TypeInt,
				Optional:     true,
				ValidateFunc: validation.IntBetween(10, 100000),
			},
			"protocol": {
				Type:         schema.TypeString,
				ValidateFunc: validation.StringInSlice([]string{"TCP", "UDP"}, false),
			},
			"description": {
				Type:             schema.TypeString,
				DiffSuppressFunc: suppressDescription,
			},
		},
	}
}

func resourceThingRead(d *schema.ResourceData, m interface{}) error {
	_ = d.Set("size_quota", resp.SizeQuota / 1024)
	if len(resp.UrlCategories) == 0 {
		_ = d.Set("url_categories", []string{"ANY"})
	}
	for i, c := range resp.SourceCountries {
		resp.SourceCountries[i] = strings.TrimPrefix(c, "COUNTRY_")
	}
	_ = d.Set("fail_open", boolToInvertedInt(d.Get("fail_open")))
	_ = d.Set("server_groups", flattenCommonServerGroupSimple(resp.ServerGroups))
	_ = d.Set("plain_groups", flattenPlainGroups(resp.PlainGroups))
	return nil
}
'''

SDKV2_SHARED = '''
package zia

func flattenCommonServerGroupSimple(groups []ServerGroup) []interface{} {
	ids := make([]string, len(groups))
	for i, g := range groups {
		ids[i] = g.ID
	}
	return []interface{}{
		map[string]interface{}{
			"id": ids,
		},
	}
}

func flattenPlainGroups(groups []Group) []interface{} {
	out := make([]interface{}, len(groups))
	for i, g := range groups {
		out[i] = map[string]interface{}{"id": g.ID}
	}
	return out
}

func flattenProfileSimple(profile *Profile) []interface{} {
	return []interface{}{
		map[string]interface{}{
			"id": profile.ID,
		},
	}
}
'''

FRAMEWORK_SOURCE = '''
package resources

func (r *thingResource) Schema(ctx context.Context) {
	resp.Schema = schema.Schema{
		Attributes: map[string]schema.Attribute{
			"log_level": schema.Int64Attribute{
				Optional: true,
				Validators: []validator.Int64{
					int64validator.Between(0, 4),
				},
			},
			"mode": schema.StringAttribute{
				Validators: []validator.String{
					stringvalidator.OneOf("strict", "permissive"),
				},
			},
		},
	}
}

func (r *thingResource) Create(ctx context.Context) {
	payload.EnableFailOpen = boolToInvertedInt(plan.EnableFailOpen.ValueBool())
}

func (r *thingResource) Read(ctx context.Context) {
	plan.Active = invertedStrToBool(p.Active)
	plan.EnableCaptivePortalDetection = invertedIntToBool(p.EnableCaptivePortalDetection)
}

func boolToInvertedInt(v bool) int {
	if v {
		return 0
	}
	return 1
}

func invertedStrToBool(v string) types.Bool {
	return types.BoolValue(v == "0")
}
'''


def _by_class(findings, klass):
    return [f for f in findings if f[2] == klass]


class ScannerBatteryTest(unittest.TestCase):
    """Every scanner class fires on its dialect's idiom, and the covered/
    MISSING verdict follows the override dict."""

    def _scan(self, source, shared="", override=None):
        findings = []
        scan_resource("zia_thing", source, shared, override or {}, findings)
        return findings

    def test_sdkv2_range_validator(self):
        hits = _by_class(self._scan(SDKV2_SOURCE), "range_validator")
        self.assertTrue(any(f[1] == "size_quota" and f[5] == "MISSING"
                            for f in hits))
        covered = self._scan(
            SDKV2_SOURCE, override={"ranges": {"size_quota": [10, 100000]}})
        hits = _by_class(covered, "range_validator")
        self.assertTrue(any(f[1] == "size_quota" and f[5] == "covered"
                            for f in hits))

    def test_sdkv2_enum_and_diff_suppress_are_informational(self):
        findings = self._scan(SDKV2_SOURCE)
        enums = _by_class(findings, "enum_validator")
        self.assertTrue(any(f[1] == "protocol" and f[5] == "info"
                            for f in enums))
        suppress = _by_class(findings, "diff_suppress")
        self.assertTrue(any(f[1] == "description" and f[5] == "info"
                            for f in suppress))

    def test_unit_conversion(self):
        findings = self._scan(SDKV2_SOURCE)
        hits = _by_class(findings, "unit_conversion")
        self.assertEqual([(f[1], f[5]) for f in hits],
                         [("size_quota", "MISSING")])
        covered = self._scan(SDKV2_SOURCE,
                             override={"divide": {"size_quota": 1024}})
        self.assertEqual(_by_class(covered, "unit_conversion")[0][5], "covered")

    def test_literal_default(self):
        findings = self._scan(SDKV2_SOURCE)
        hits = _by_class(findings, "literal_default")
        self.assertEqual([(f[1], f[5]) for f in hits],
                         [("url_categories", "MISSING")])
        covered = self._scan(
            SDKV2_SOURCE, override={"defaults": {"url_categories": ["ANY"]}})
        self.assertEqual(_by_class(covered, "literal_default")[0][5], "covered")

    def test_strip_prefix(self):
        findings = self._scan(SDKV2_SOURCE)
        hits = _by_class(findings, "strip_prefix")
        self.assertEqual([(f[3], f[5]) for f in hits],
                         [("COUNTRY_", "MISSING")])
        covered = self._scan(
            SDKV2_SOURCE,
            override={"strip_prefix": {"source_countries": "COUNTRY_"}})
        self.assertEqual(_by_class(covered, "strip_prefix")[0][5], "covered")

    def test_inverted_bool_sdkv2_dget_form(self):
        findings = self._scan(SDKV2_SOURCE)
        hits = _by_class(findings, "int_bool_inverted")
        self.assertTrue(any(f[1] == "fail_open" and f[5] == "MISSING"
                            for f in hits))
        covered = self._scan(SDKV2_SOURCE,
                             override={"invert_bool": ["fail_open"]})
        hits = _by_class(covered, "int_bool_inverted")
        self.assertTrue(any(f[1] == "fail_open" and f[5] == "covered"
                            for f in hits))

    def test_inverted_bool_framework_both_directions(self):
        # Real zcc idioms: write boolToInvertedX(plan.F.ValueBool()),
        # read invertedXToBool(p.F). The helper DEFINITIONS in the same
        # file must not produce phantom fields, and a field hit by both
        # directions appears once.
        findings = self._scan(FRAMEWORK_SOURCE)
        hits = _by_class(findings, "int_bool_inverted")
        self.assertEqual(sorted(f[1] for f in hits),
                         ["active", "enable_captive_portal_detection",
                          "enable_fail_open"])
        covered = self._scan(
            FRAMEWORK_SOURCE,
            override={"invert_bool": ["active", "enable_fail_open"]})
        verdicts = {f[1]: f[5]
                    for f in _by_class(covered, "int_bool_inverted")}
        self.assertEqual(verdicts["enable_fail_open"], "covered")
        self.assertEqual(verdicts["enable_captive_portal_detection"],
                         "MISSING")

    def test_merge_flatten_only_for_slice_singleton_helpers(self):
        findings = self._scan(SDKV2_SOURCE, shared=SDKV2_SHARED)
        hits = _by_class(findings, "merge_flatten")
        # flattenPlainGroups returns per-element maps — must NOT fire.
        # flattenProfileSimple returns a singleton but takes a single
        # POINTER (one-in-one-out is not a merge) — must NOT fire either:
        # that false positive flagged zpn_er_id/flattenCustomIDSet live.
        self.assertEqual([(f[1], f[3], f[5]) for f in hits],
                         [("server_groups", "flattenCommonServerGroupSimple",
                           "MISSING")])
        covered = self._scan(SDKV2_SOURCE, shared=SDKV2_SHARED,
                             override={"merge_blocks": ["server_groups"]})
        self.assertEqual(_by_class(covered, "merge_flatten")[0][5], "covered")

    def test_merge_flatten_schema_single_block_is_covered(self):
        # ZIA ID-group blocks are max_items=1 in the schema — the
        # transform auto-merges them (block_is_single); no override needed.
        findings = []
        scan_resource(
            "zia_thing", SDKV2_SOURCE, SDKV2_SHARED, {}, findings,
            block_types={"server_groups": {"nesting_mode": "list",
                                           "max_items": 1}})
        hits = _by_class(findings, "merge_flatten")
        self.assertEqual(hits[0][5], "covered")
        self.assertIn("schema-single", hits[0][3])

    def test_merge_flatten_non_block_field_is_informational(self):
        # d.Set target that is not a config block (computed attribute):
        # merge_blocks cannot encode it; report as info, never fatal.
        findings = []
        scan_resource("zia_thing", SDKV2_SOURCE, SDKV2_SHARED, {}, findings,
                      block_types={})
        hits = _by_class(findings, "merge_flatten")
        self.assertEqual(hits[0][5], "info")

    def test_framework_validators(self):
        findings = self._scan(FRAMEWORK_SOURCE)
        ranges = _by_class(findings, "range_validator")
        self.assertTrue(any(f[1] == "log_level" for f in ranges))
        enums = _by_class(findings, "enum_validator")
        self.assertTrue(any(f[1] == "mode" for f in enums))


class HelperTest(unittest.TestCase):
    def test_find_merge_flatten_helpers(self):
        self.assertEqual(find_merge_flatten_helpers(SDKV2_SHARED),
                         {"flattenCommonServerGroupSimple"})

    def test_nearest_field_both_dialects(self):
        sdkv2_pos = SDKV2_SOURCE.index("IntBetween")
        self.assertEqual(nearest_field(SDKV2_SOURCE, sdkv2_pos), "size_quota")
        fw_pos = FRAMEWORK_SOURCE.index("int64validator")
        self.assertEqual(nearest_field(FRAMEWORK_SOURCE, fw_pos), "log_level")
        self.assertEqual(nearest_field("no fields here", 5), "?")


class BaselineTest(unittest.TestCase):
    """report(): only MISSING-and-not-baselined is fatal (exit 4); the
    baseline file is acknowledged_drops for the miner."""

    FINDING = ("zia_thing", "size_quota", "unit_conversion",
               "resp.SizeQuota / 1024", "divide", "MISSING")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.baseline = os.path.join(self.tmp, "mine-baseline.json")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_missing_without_baseline_exits_4(self):
        out = io.StringIO()
        code = report([self.FINDING], [], self.baseline, False, out)
        self.assertEqual(code, 4)
        self.assertIn("[MISSING]", out.getvalue())
        self.assertIn("1 NEW missing coverage", out.getvalue())

    def test_baselined_missing_is_not_fatal(self):
        with open(self.baseline, "w", encoding="utf-8") as f:
            json.dump(["zia_thing|size_quota|unit_conversion|divide"], f)
        out = io.StringIO()
        code = report([self.FINDING], [], self.baseline, False, out)
        self.assertEqual(code, 0)
        self.assertIn("[MISSING baseline]", out.getvalue())

    def test_covered_exits_0(self):
        covered = self.FINDING[:5] + ("covered",)
        out = io.StringIO()
        self.assertEqual(report([covered], [], self.baseline, False, out), 0)

    def test_update_baseline_writes_signatures(self):
        out = io.StringIO()
        report([self.FINDING], [], self.baseline, True, out)
        with open(self.baseline, encoding="utf-8") as f:
            self.assertEqual(json.load(f),
                             ["zia_thing|size_quota|unit_conversion|divide"])

    def test_fetch_failures_reported_not_fatal(self):
        out = io.StringIO()
        code = report([], ["zpa_ghost"], self.baseline, False, out)
        self.assertEqual(code, 0)
        self.assertIn("zpa_ghost", out.getvalue())
        self.assertIn("1 resource(s) unfetchable", out.getvalue())


if __name__ == "__main__":
    unittest.main()

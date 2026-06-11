"""Tests for tools/conformance.py — the future-module gate.

The headline test iterates EVERY generated registry type, synthesizes
adversarial raw items, and conformance-checks them through the real pipeline.
A new registry entry is therefore automatically synthesized and verified
against the full quirk catalog with no hand-written fixture. The remaining
tests pin synthesis determinism, the camelCase inversion helper, and that
synthesis is genuinely adversarial (so a regression making it trivial is
caught). All synthesized data is fictional.
"""
import json
import unittest

from tools.conformance import (
    camel,
    conformance_check,
    synthesize_item,
)
from tools.registry import generated_types
from tools.tfschema import load_resource
from tools.transform import snake


class ConformanceGateTest(unittest.TestCase):
    def test_every_generated_type_conforms(self):
        # The gate: schema-driven adversarial synthesis -> full transform ->
        # nesting-aware typecheck, ZERO mismatches, for every current AND
        # future resource. A new generate=true entry is covered automatically.
        types = generated_types()
        self.assertTrue(types, "registry has no generated types")
        for rt in types:
            ok, detail = conformance_check(rt)
            self.assertTrue(ok, "%s failed conformance:\n%s" % (rt, detail))


class SynthesisDeterminismTest(unittest.TestCase):
    def test_same_seed_yields_identical_bytes(self):
        # No RNG: re-running synthesis with the same seed must be byte-for-byte
        # identical, so conformance output is reproducible and goldens stable.
        for rt in generated_types():
            a = json.dumps(synthesize_item(rt, 1), sort_keys=True)
            b = json.dumps(synthesize_item(rt, 1), sort_keys=True)
            self.assertEqual(a, b, "%s synthesis not deterministic" % rt)

    def test_different_seeds_diverge(self):
        # Distinct seeds must produce distinct items so 3 synthesized items
        # never collide on a derived map key.
        for rt in generated_types():
            one = json.dumps(synthesize_item(rt, 1), sort_keys=True)
            two = json.dumps(synthesize_item(rt, 2), sort_keys=True)
            self.assertNotEqual(one, two, "%s seeds 1 and 2 identical" % rt)


class CamelInversionTest(unittest.TestCase):
    def test_round_trips_through_snake(self):
        # camel() must produce a pre-image of snake(): feeding the result back
        # through snake() reproduces the input. That is the only property the
        # pipeline relies on (it snake_cases keys before anything else).
        for name in [
            "name",
            "condition_type",
            "dns_server_ips",
            "forwarding_profile_actions",
            "system_proxy_data",
            "url_keyword_counts",
            "enable_lwf_driver",
            "retain_parent_url_count",
            "ssid",
        ]:
            self.assertEqual(snake(camel(name)), name, name)

    def test_every_schema_input_name_round_trips(self):
        # Exhaustive: every input attribute and block name across all
        # generated resources must survive camel()->snake().
        def walk(block):
            attrs = block.get("attributes") or {}
            for n in attrs:
                self.assertEqual(snake(camel(n)), n, n)
            for bn, bt in (block.get("block_types") or {}).items():
                self.assertEqual(snake(camel(bn)), bn, bn)
                walk(bt["block"])

        for rt in generated_types():
            walk(load_resource(rt)["block"])


class ConformanceCatchesRegressionsTest(unittest.TestCase):
    """Pin that the gate goes RED when the single-block contract regresses.

    If a future transform change re-emits single-mode blocks as one-element
    lists (the old broken shape that reached terraform as a tuple), the
    conformance check must fail — otherwise a refactor could quietly make the
    harness vacuous and a green run would prove nothing.
    """

    def test_relisted_single_blocks_fail_conformance(self):
        import tools.transform as transform_mod

        block = load_resource("zcc_forwarding_profile")["block"]

        def relist_single_blocks(item, blk):
            # Reintroduce the regression: every single-mode block dict
            # becomes a one-element list, recursing through list blocks.
            for bname, bt in (blk.get("block_types") or {}).items():
                value = item.get(bname)
                if value is None:
                    continue
                inner = bt["block"]
                if bt["nesting_mode"] == "single":
                    if isinstance(value, dict):
                        relist_single_blocks(value, inner)
                        item[bname] = [value]
                elif isinstance(value, list):
                    for elem in value:
                        if isinstance(elem, dict):
                            relist_single_blocks(elem, inner)

        real = transform_mod.transform_items

        def broken(raw, resource_type, override):
            items, originals, drops = real(raw, resource_type, override)
            for it in items.values():
                relist_single_blocks(it, block)
            return items, originals, drops

        transform_mod.transform_items = broken
        try:
            ok, detail = conformance_check("zcc_forwarding_profile")
        finally:
            transform_mod.transform_items = real

        self.assertFalse(
            ok, "conformance stayed green despite re-listed single blocks"
        )
        self.assertIn("single block", detail)


class RefObjectMemberConformanceTest(unittest.TestCase):
    """Close the synthesis gap: _syn_object_list_attr never emits a {id,name}
    reference object as an object-list MEMBER, so the _coerce_object_members
    ref-unwrap path (transform.py) is real code conformance never covers. A
    regression there would pass the gate. Patch synthesis to inject one ref
    member and verify the full pipeline still conforms."""

    def test_ref_object_as_object_list_member_conforms(self):
        import tools.conformance as conformance_mod

        real = conformance_mod.synthesize_item

        def with_ref_member(resource_type, seed):
            item = real(resource_type, seed)
            ports = item.get("tcpPortRange")
            if isinstance(ports, list) and ports and isinstance(ports[0], dict):
                # Replace the 'from' member with a {id,name} ref object whose
                # id is a numeric string — coerce must unwrap to "8080".
                ports[0]["from"] = {"id": "8080", "name": "x"}
            return item

        conformance_mod.synthesize_item = with_ref_member
        try:
            ok, detail = conformance_check("zpa_application_segment")
        finally:
            conformance_mod.synthesize_item = real

        self.assertTrue(
            ok, "ref-object object-list member broke conformance:\n%s" % detail
        )


class SynthesisIsAdversarialTest(unittest.TestCase):
    """Guard against a regression that quietly makes synthesis trivial: the
    raw items must actually contain the quirky shapes the harness exists to
    exercise, otherwise a green run would prove nothing."""

    def test_keys_are_camelcase_not_snake(self):
        # zcc_forwarding_profile has multi-word fields; the raw item must use
        # camelCase API keys (so snake_keys is exercised), never snake_case.
        item = synthesize_item("zcc_forwarding_profile", 1)
        self.assertIn("forwardingProfileActions", item)
        self.assertNotIn("forwarding_profile_actions", item)

    def test_emits_quirky_primitive_shapes(self):
        # Across three seeds the bool/number/string fields must collectively
        # emit the adversarial encodings (int-as-bool, string-as-number,
        # reference objects), not just clean values.
        raws = [synthesize_item("zcc_forwarding_profile", s) for s in (1, 2, 3)]
        flat = json.dumps(raws)
        # ref objects {"id": ...} present (quirks 9/10)
        self.assertIn('"id"', flat)
        # at least one bool-as-string and one bool-as-int somewhere
        bool_field_values = [r.get("evaluateTrustedNetwork") for r in raws]
        self.assertTrue(
            any(isinstance(v, str) for v in bool_field_values)
            or any(isinstance(v, int) and not isinstance(v, bool) for v in bool_field_values),
            "no adversarial bool encodings emitted: %r" % bool_field_values,
        )

    def test_csv_field_emits_comma_string_for_split_csv_resource(self):
        # zcc_trusted_network has split_csv fields; at least one seed must
        # emit a comma-joined string for one of them (quirk 7), and the
        # transform must split it back to a list.
        from tools.transform import load_override, transform_items

        raws = [synthesize_item("zcc_trusted_network", s) for s in range(8)]
        emitted_csv = any(
            isinstance(r.get("dnsServers"), str) and "," in r.get("dnsServers", "")
            for r in raws
        )
        self.assertTrue(emitted_csv, "no CSV string emitted for a split_csv field")
        ov = load_override("zcc_trusted_network")
        items, _, _ = transform_items(raws, "zcc_trusted_network", ov)
        # every dns_server_ips that came from CSV is a real list now
        for it in items.values():
            self.assertIsInstance(it.get("dns_server_ips", []), list)

    def test_max_items_one_block_emits_multi_element_and_merges(self):
        # zia_cloud_app_control_rule's ID-group blocks (departments etc.)
        # are max_items=1: synthesis must emit the multi-element API shape
        # at some seed (quirk 14), and the transform must merge it into ONE
        # object — the pre-fix pipeline emitted N blocks and terraform core
        # rejected them with "Too many departments blocks".
        from tools.transform import load_override, transform_items

        raws = [synthesize_item("zia_cloud_app_control_rule", s) for s in range(6)]
        multi = [
            r for r in raws
            if isinstance(r.get("departments"), list) and len(r["departments"]) > 1
        ]
        self.assertTrue(
            multi, "no multi-element list emitted for a max_items=1 block"
        )
        ov = load_override("zia_cloud_app_control_rule")
        items, _, _ = transform_items(raws, "zia_cloud_app_control_rule", ov)
        for it in items.values():
            if it.get("departments") is not None:
                self.assertIsInstance(it["departments"], dict)

    def test_null_stub_emitted_and_dropped(self):
        # quirk 15: zia_location_management's extranet blocks must appear
        # as id-zero stubs at some seed, and NO null-object may survive
        # the transform — the provider's flattener treats them as absent.
        from tools.transform import (
            _is_null_object, load_override, transform_items,
        )

        raws = [synthesize_item("zia_location_management", s) for s in range(8)]
        flat = json.dumps(raws)
        self.assertIn('"id": 0', flat, "no id-zero stub emitted by synthesis")
        ov = load_override("zia_location_management")
        items, _, _ = transform_items(raws, "zia_location_management", ov)

        def walk(v):
            if isinstance(v, dict):
                self.assertFalse(_is_null_object(v), "stub survived: %r" % v)
                for x in v.values():
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)

        for it in items.values():
            walk(it)

    def test_list_block_emitted_as_bare_dict_at_some_seed(self):
        # The single-dict-for-list-block wrap (quirk 13) must be exercised:
        # across seeds, forwardingProfileActions appears at least once as a
        # bare dict (not a list).
        shapes = [
            type(synthesize_item("zcc_forwarding_profile", s).get("forwardingProfileActions")).__name__
            for s in range(6)
        ]
        self.assertIn("dict", shapes, "list block never emitted as bare dict")
        self.assertIn("list", shapes, "list block never emitted as a list")


if __name__ == "__main__":
    unittest.main()

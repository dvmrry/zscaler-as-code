"""End-to-end pipeline test over demo data derived from Zscaler's own
public SDK test cassettes (see fixtures/demo/README.md). Catches
realistic-shape regressions the hand-built fixtures may miss."""
import json
import os
import unittest

from tools.registry import generated_types
from tools.tfschema import classify_attributes, load_resource
from tools.transform import load_override, render_imports, render_tfvars, transform_items

DEMO_DIR = os.path.join("tools", "tests", "fixtures", "demo")


def _demo_types():
    if not os.path.isdir(DEMO_DIR):
        return []
    return sorted(
        f[:-len(".json")] for f in os.listdir(DEMO_DIR) if f.endswith(".json")
    )


class DemoPipelineTest(unittest.TestCase):
    def test_demo_files_exist_for_generated_types(self):
        missing = [rt for rt in generated_types() if rt not in _demo_types()]
        # Every generated resource should eventually have demo coverage;
        # tolerate gaps explicitly so additions are deliberate.
        self.assertEqual(
            missing, [],
            "generated types without demo data: %r (extract from the SDK "
            "cassettes or document why not)" % missing,
        )

    def test_pipeline_handles_demo_data(self):
        for rt in _demo_types():
            with open(os.path.join(DEMO_DIR, rt + ".json")) as f:
                raw = json.load(f)
            self.assertTrue(raw, "%s demo file is empty" % rt)
            override = load_override(rt)
            items, originals, drops = transform_items(raw, rt, override)
            self.assertTrue(items, "%s produced no items" % rt)
            # determinism: byte-identical double run
            again, _, _ = transform_items(raw, rt, override)
            self.assertEqual(render_tfvars(items), render_tfvars(again), rt)
            # every emitted key is a module input
            block = load_resource(rt)["block"]
            cls = classify_attributes(block)
            allowed = set(cls["required"] + cls["optional"]) | set(
                (block.get("block_types") or {})
            )
            for key, item in items.items():
                unknown = set(item) - allowed
                self.assertEqual(
                    unknown, set(),
                    "%s item %r emitted non-input keys %r" % (rt, key, unknown),
                )
            # imports render with the resource's template
            text = render_imports(rt, originals, override)
            self.assertIn('module.%s.%s.this[' % (rt, rt), text)


if __name__ == "__main__":
    unittest.main()

"""Offline tests for the SDK surface sweep (tools/surface.py).

No network: the parser/synthesizer/root-pick run over synthetic Go
text; the live recall (the ipv6_dns_64prefix catch) is pinned in
test_transform's Ipv6Dns64PrefixRenameTest.
"""
import unittest

from tools.surface import (
    neutralize_skip_if, parse_structs, pick_root, synth_struct,
)

GO_TEXT = '''
package servergroup

type ServerGroup struct {
	ID                 string               `json:"id,omitempty"`
	Name               string               `json:"name,omitempty"`
	Enabled            bool                 `json:"enabled"`
	Description        string               `json:"description,omitempty"`
	IpAnchored         bool                 `json:"ipAnchored"`
	DynamicDiscovery   bool                 `json:"dynamicDiscovery"`
	Applications       []Applications       `json:"applications,omitempty"`
	AppConnectorGroups []common.External    `json:"appConnectorGroups,omitempty"`
	CreationTime       string               `json:"creationTime,omitempty"`
	Predefined         bool                 `json:"predefined"`
}

type Applications struct {
	ID   string `json:"id,omitempty"`
	Name string `json:"name,omitempty"`
}

type Unrelated struct {
	Foo string `json:"foo"`
}
'''


class ParseSynthTest(unittest.TestCase):
    def test_parses_structs_and_tags(self):
        reg = parse_structs(GO_TEXT)
        self.assertIn("ServerGroup", reg)
        tags = [t for t, _ in reg["ServerGroup"]]
        self.assertIn("ipAnchored", tags)
        self.assertEqual([t for t, _ in reg["Applications"]], ["id", "name"])

    def test_synth_builds_maximal_item(self):
        reg = parse_structs(GO_TEXT)
        item = synth_struct("ServerGroup", reg)
        self.assertEqual(item["enabled"], True)
        self.assertEqual(item["applications"], [{"id": "x", "name": "x"}])
        # unresolved cross-package type gets the reference shape
        self.assertEqual(item["appConnectorGroups"],
                         [{"id": "1", "name": "x"}])

    def test_root_pick_prefers_schema_overlap(self):
        reg = parse_structs(GO_TEXT)
        self.assertEqual(pick_root(reg, "zpa_server_group"), "ServerGroup")

    def test_skip_if_neutralized(self):
        item = {"predefined": True, "name": "x"}
        out = neutralize_skip_if(item, {"skip_if": [{"predefined": True}]})
        self.assertIs(out["predefined"], False)

    def test_recursion_depth_capped(self):
        reg = parse_structs('''
type A struct {
	Child []A `json:"child"`
	ID    string `json:"id"`
}
''')
        item = synth_struct("A", reg)  # must terminate
        self.assertIn("child", item)


if __name__ == "__main__":
    unittest.main()

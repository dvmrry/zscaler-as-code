"""Tests for tools/bump_check.py — offline (sources injected)."""
import unittest

from tools.bump_check import check, is_newer, latest_of


class IsNewerTest(unittest.TestCase):
    def test_core_ordering(self):
        self.assertTrue(is_newer("4.7.25", "4.7.24"))
        self.assertTrue(is_newer("4.8.0", "4.7.24"))
        self.assertTrue(is_newer("5.0.0", "4.7.24"))
        self.assertFalse(is_newer("4.7.24", "4.7.24"))
        self.assertFalse(is_newer("4.7.23", "4.7.24"))

    def test_short_core_pads(self):
        self.assertTrue(is_newer("4.8", "4.7.24"))
        self.assertFalse(is_newer("4.7", "4.7.0"))

    def test_release_beats_prerelease_same_core(self):
        self.assertTrue(is_newer("0.1.0", "0.1.0-beta.1"))
        self.assertFalse(is_newer("0.1.0-beta.1", "0.1.0"))

    def test_prerelease_chunk_ordering(self):
        self.assertTrue(is_newer("0.1.0-beta.2", "0.1.0-beta.1"))
        self.assertTrue(is_newer("0.1.1-alpha.1", "0.1.0-beta.9"))
        self.assertFalse(is_newer("0.1.0-beta.1", "0.1.0-beta.1"))


class LatestOfTest(unittest.TestCase):
    VERSIONS = ["4.7.24", "4.8.0", "5.0.0-rc.1", "4.6.0"]

    def test_stable_only(self):
        self.assertEqual(latest_of(self.VERSIONS, False), "4.8.0")

    def test_including_prerelease(self):
        self.assertEqual(latest_of(self.VERSIONS, True), "5.0.0-rc.1")

    def test_empty(self):
        self.assertIsNone(latest_of([], True))


class CheckTest(unittest.TestCase):
    PINS = {"zia": "4.7.24", "zpa": "4.4.4", "zcc": "0.1.0-beta.1"}

    @staticmethod
    def registry(versions_by_provider):
        return lambda name: versions_by_provider[name]

    def test_all_current(self):
        reg = self.registry({
            "zia": ["4.7.24", "4.7.0"],
            "zpa": ["4.4.4"],
            "zcc": ["0.1.0-beta.1"],
        })
        lines, updates = check(
            self.PINS, registry=reg, tf_releases=lambda: ["1.15.4"],
            tf_current="1.15.4")
        self.assertEqual(updates, 0)
        self.assertTrue(any("ok terraform" in l for l in lines))

    def test_provider_update_detected_with_runbook_pointer(self):
        reg = self.registry({
            "zia": ["4.7.24", "4.7.25"],
            "zpa": ["4.4.4"],
            "zcc": ["0.1.0-beta.1"],
        })
        lines, updates = check(
            self.PINS, registry=reg, tf_releases=lambda: ["1.15.4"],
            tf_current="1.15.4")
        self.assertEqual(updates, 1)
        update_lines = [l for l in lines if l.startswith("UPDATE")]
        self.assertEqual(len(update_lines), 1)
        self.assertIn("4.7.24 -> 4.7.25", update_lines[0])
        self.assertIn("Provider Bumps", update_lines[0])

    def test_prerelease_pin_sees_newer_prerelease(self):
        # zcc is pinned to a beta: a newer beta counts as an update.
        reg = self.registry({
            "zia": ["4.7.24"],
            "zpa": ["4.4.4"],
            "zcc": ["0.1.0-beta.1", "0.1.0-beta.2"],
        })
        lines, updates = check(
            self.PINS, registry=reg, tf_releases=lambda: ["1.15.4"],
            tf_current="1.15.4")
        self.assertEqual(updates, 1)
        self.assertTrue(any("0.1.0-beta.2" in l for l in lines))

    def test_stable_pin_ignores_prerelease_but_notes_it(self):
        reg = self.registry({
            "zia": ["4.7.24", "5.0.0-rc.1"],
            "zpa": ["4.4.4"],
            "zcc": ["0.1.0-beta.1"],
        })
        lines, updates = check(
            self.PINS, registry=reg, tf_releases=lambda: ["1.15.4"],
            tf_current="1.15.4")
        self.assertEqual(updates, 0)
        self.assertTrue(any("prerelease 5.0.0-rc.1 exists" in l for l in lines))

    def test_terraform_update_detected(self):
        reg = self.registry({
            "zia": ["4.7.24"], "zpa": ["4.4.4"], "zcc": ["0.1.0-beta.1"],
        })
        lines, updates = check(
            self.PINS, registry=reg,
            tf_releases=lambda: ["1.15.4", "1.15.6", "1.16.0-beta1"],
            tf_current="1.15.4")
        self.assertEqual(updates, 1)
        self.assertTrue(any("UPDATE terraform: 1.15.4 -> 1.15.6" in l for l in lines))

    def test_no_local_terraform_is_informational_not_update(self):
        reg = self.registry({
            "zia": ["4.7.24"], "zpa": ["4.4.4"], "zcc": ["0.1.0-beta.1"],
        })
        lines, updates = check(
            self.PINS, registry=reg, tf_releases=lambda: ["1.15.6"],
            tf_current=None)
        self.assertEqual(updates, 0)
        self.assertTrue(any("no local binary" in l for l in lines))

    def test_empty_registry_counts_as_update_loudly(self):
        # Checking nothing is never success.
        reg = self.registry({"zia": [], "zpa": ["4.4.4"], "zcc": ["0.1.0-beta.1"]})
        lines, updates = check(
            self.PINS, registry=reg, tf_releases=lambda: ["1.15.4"],
            tf_current="1.15.4")
        self.assertEqual(updates, 1)
        self.assertTrue(any("no versions" in l for l in lines))



class TfReleasesFailureTest(unittest.TestCase):
    def test_provider_results_survive_tf_index_failure(self):
        # a terraform index failure after the provider phase must not
        # discard the provider lines (partial output beats a bare
        # "bump check failed")
        from tools.bump_check import check

        def boom():
            raise RuntimeError("connection reset")

        reg = lambda provider: ["4.7.24"] if provider == "zia" else ["9.9.9"]
        lines, updates = check(
            {"zia": "4.7.24", "zpa": "4.4.4", "zcc": "0.1.0-beta.1"},
            registry=reg, tf_releases=boom, tf_current="1.15.4")
        self.assertTrue(any("zia" in l for l in lines))
        self.assertTrue(any("release index unreachable" in l for l in lines))
        self.assertGreaterEqual(updates, 1)


if __name__ == "__main__":
    unittest.main()

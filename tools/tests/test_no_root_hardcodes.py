import glob
import os
import re
import unittest

# Tools that legitimately reference root config/ at module load (the resolver
# itself, and demo materialization) are exempt by explicit allowlist.
EXEMPT = {"deployment.py"}
PATTERN = re.compile(r'os\.path\.join\(\s*["\'](config|imports|envs)["\']\s*,\s*\w*tenant')


class NoRootHardcodesTest(unittest.TestCase):
    def test_no_tool_hardcodes_root_tenant_join(self):
        offenders = []
        for path in glob.glob(os.path.join("tools", "*.py")):
            if os.path.basename(path) in EXEMPT:
                continue
            src = open(path, encoding="utf-8").read()
            if PATTERN.search(src):
                offenders.append(path)
        self.assertEqual(offenders, [], "root-hardcoded tenant joins remain: %s" % offenders)


if __name__ == "__main__":
    unittest.main()

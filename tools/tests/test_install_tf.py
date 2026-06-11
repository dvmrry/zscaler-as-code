"""Tests for tools/install_tf.py — all offline (fetch is injected)."""
import hashlib
import io
import os
import tempfile
import unittest
import zipfile

from tools.install_tf import (
    detect_platform,
    expected_sha,
    install,
    sums_url,
    zip_url,
)


def _fake_release(version, plat):
    """Build an in-memory terraform release: (zip bytes, SHA256SUMS text)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("terraform", "#!/bin/sh\necho fake terraform\n")
    blob = buf.getvalue()
    sums = "%s  terraform_%s_%s.zip\n" % (
        hashlib.sha256(blob).hexdigest(), version, plat,
    )
    return blob, sums


class DetectPlatformTest(unittest.TestCase):
    def test_known_platforms(self):
        self.assertEqual(detect_platform("Darwin", "arm64"), "darwin_arm64")
        self.assertEqual(detect_platform("Linux", "x86_64"), "linux_amd64")
        self.assertEqual(detect_platform("Linux", "aarch64"), "linux_arm64")
        self.assertEqual(detect_platform("Darwin", "x86_64"), "darwin_amd64")

    def test_unknown_platform_raises(self):
        with self.assertRaises(ValueError):
            detect_platform("SunOS", "sparc")


class UrlTest(unittest.TestCase):
    def test_urls_pin_version_and_platform(self):
        self.assertEqual(
            zip_url("1.15.4", "darwin_arm64"),
            "https://releases.hashicorp.com/terraform/1.15.4/"
            "terraform_1.15.4_darwin_arm64.zip",
        )
        self.assertIn("terraform_1.15.4_SHA256SUMS", sums_url("1.15.4"))


class ExpectedShaTest(unittest.TestCase):
    def test_finds_matching_line(self):
        sums = (
            "aaa  terraform_1.15.4_linux_amd64.zip\n"
            "bbb  terraform_1.15.4_darwin_arm64.zip\n"
        )
        self.assertEqual(expected_sha(sums, "1.15.4", "darwin_arm64"), "bbb")

    def test_missing_platform_raises(self):
        with self.assertRaises(ValueError):
            expected_sha("aaa  terraform_1.15.4_linux_amd64.zip\n", "1.15.4", "windows_amd64")


class InstallTest(unittest.TestCase):
    def test_verifies_and_extracts(self):
        blob, sums = _fake_release("9.9.9", "linux_amd64")

        def fetch(url):
            return sums.encode() if url.endswith("SHA256SUMS") else blob

        with tempfile.TemporaryDirectory() as td:
            path = install("9.9.9", td, fetch=fetch, plat="linux_amd64")
            self.assertTrue(os.path.exists(path))
            self.assertTrue(os.access(path, os.X_OK))

    def test_checksum_mismatch_refuses(self):
        blob, _ = _fake_release("9.9.9", "linux_amd64")
        bad_sums = "0" * 64 + "  terraform_9.9.9_linux_amd64.zip\n"

        def fetch(url):
            return bad_sums.encode() if url.endswith("SHA256SUMS") else blob

        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                install("9.9.9", td, fetch=fetch, plat="linux_amd64")


if __name__ == "__main__":
    unittest.main()

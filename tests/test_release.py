import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "config").mkdir()
        self.entries = ["README.md", release.MANIFEST]
        self.manifest(self.entries)
        (self.root / "README.md").write_text("# Fictional public project\n", encoding="utf-8")

    def manifest(self, entries):
        (self.root / release.MANIFEST).write_text(json.dumps(entries), encoding="utf-8")

    def inspect(self):
        return release.inspect_project(self.root)

    def test_complete_project_has_no_default_findings(self):
        contents, findings, index_checked = release.inspect_project(ROOT)
        self.assertFalse(findings, findings)
        self.assertEqual(set(contents), set(release.read_manifest(ROOT)))

    def test_minimal_allowlisted_project_passes(self):
        contents, findings, index_checked = self.inspect()
        self.assertEqual(len(contents), 2)
        self.assertEqual(findings, [])

    def test_phone_and_other_identifiers_are_detected_without_echo(self):
        samples = {
            "phone": "1" + "38" + "1234" + "5678",
            "user_home_path": "/" + "Users/" + "fixture/private",
            "windows_home_path": "C:" + "\\" + "Users" + "\\" + "fixture",
            "live_job_link": "job_detail/" + "a" * 14,
            "platform_identity": "ou_" + "a" * 16,
            "cloud_credential": "AK" + "IA" + "A" * 16,
            "private_key": "-" * 5 + "BEGIN " + "PRIVATE KEY" + "-" * 5,
            "assigned_credential": "api_key = " + "A" * 24,
            "non_example_email": "fixture-person@" + "not-public.test",
        }
        for expected_rule, sample in samples.items():
            with self.subTest(rule=expected_rule):
                findings = release.scan_content("fixture.md", sample.encode())
                self.assertIn(expected_rule, [item["rule"] for item in findings])
                self.assertNotIn(sample, json.dumps(findings))

    def test_example_email_domain_is_allowed(self):
        content = ("fixture@" + "example.org").encode()
        self.assertEqual(release.scan_content("fixture.md", content), [])

    def test_private_denylist_is_case_insensitive(self):
        findings = release.scan_content("fixture.md", b"PrivateMarker", ("privatemarker",))
        self.assertEqual(findings[0]["rule"], "private_deny_term")

    def test_binary_and_non_utf8_content_are_blocked(self):
        self.assertEqual(release.scan_content("fixture.md", b"\xff")[0]["rule"], "non_utf8_content")
        self.assertEqual(release.scan_content("fixture.md", b"text\x00")[0]["rule"], "binary_content")

    def test_oversized_public_file_is_blocked(self):
        findings = release.scan_content("fixture.md", b"a" * (release.MAX_PUBLIC_BYTES + 1))
        self.assertEqual(findings[0]["rule"], "oversized_public_file")

    def test_unexpected_file_blocks_export_even_if_ignored_by_git(self):
        (self.root / ".env").write_text("fixture", encoding="utf-8")
        contents, findings, index_checked = self.inspect()
        self.assertIn("unexpected_public_file", [item["rule"] for item in findings])

    def test_private_material_is_never_read_or_exported(self):
        private_root = self.root / "private"
        private_root.mkdir()
        (private_root / "data.bin").write_bytes(b"\xff\x00")
        contents, findings, index_checked = self.inspect()
        self.assertEqual(findings, [])
        self.assertEqual(set(contents), set(self.entries))

    def test_manifest_rejects_traversal_private_paths_and_binary_files(self):
        for name in ("../escape.md", "/tmp/escape.md", "private/profile.md", "portrait.png", "config\\profile.json"):
            with self.subTest(path=name):
                self.manifest([release.MANIFEST, name])
                with self.assertRaises(ValueError):
                    release.read_manifest(self.root)

    def test_manifest_rejects_duplicates_and_missing_self(self):
        for entries in (["README.md"], [release.MANIFEST, release.MANIFEST]):
            self.manifest(entries)
            with self.assertRaises(ValueError):
                release.read_manifest(self.root)

    def test_missing_public_file_is_blocked(self):
        self.manifest(self.entries + ["missing.md"])
        contents, findings, index_checked = self.inspect()
        self.assertIn("missing_or_unsafe_public_file", [item["rule"] for item in findings])

    def test_symlinked_public_file_is_blocked(self):
        target = self.root / "target.md"
        target.write_text("fixture", encoding="utf-8")
        (self.root / "linked.md").symlink_to(target)
        self.manifest(self.entries + ["target.md", "linked.md"])
        contents, findings, index_checked = self.inspect()
        self.assertIn("symlink", [item["rule"] for item in findings])
        self.assertNotIn("linked.md", contents)

    def test_symlinked_public_directory_is_not_followed(self):
        (self.root / "docs").symlink_to(self.root / "config", target_is_directory=True)
        contents, findings, index_checked = self.inspect()
        self.assertIn("symlink", [item["rule"] for item in findings])

    def test_export_is_reproducible_and_only_contains_allowlist(self):
        contents, findings, index_checked = self.inspect()
        first = release.write_export(self.root, contents, Path("exports/first.zip"))
        second = release.write_export(self.root, contents, Path("exports/second.zip"))
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with zipfile.ZipFile(first) as archive:
            names = archive.namelist()
            self.assertEqual(set(names), {f"{release.ARCHIVE_ROOT}/{name}" for name in self.entries})
            self.assertIsNone(archive.testzip())

    def test_export_does_not_overwrite(self):
        contents, findings, index_checked = self.inspect()
        destination = Path("exports/demo.zip")
        release.write_export(self.root, contents, destination)
        with self.assertRaises(ValueError):
            release.write_export(self.root, contents, destination)

    def test_export_rejects_external_path_and_symlinked_output(self):
        contents, findings, index_checked = self.inspect()
        with self.assertRaises(ValueError):
            release.write_export(self.root, contents, self.root / "outside.zip")
        (self.root / "exports").symlink_to(self.root / "config", target_is_directory=True)
        with self.assertRaises(ValueError):
            release.write_export(self.root, contents, Path("exports/demo.zip"))

    def test_git_index_blocks_previously_staged_secret(self):
        secret = ("1" + "38" + "1234" + "5678").encode()
        responses = [
            subprocess.CompletedProcess([], 0, str(self.root).encode(), b""),
            subprocess.CompletedProcess([], 0, b"100644 fixture 0\tREADME.md\x00", b""),
            subprocess.CompletedProcess([], 0, secret, b""),
        ]
        with patch.object(release.shutil, "which", return_value="git"), patch.object(release.subprocess, "run", side_effect=responses):
            findings, checked = release.check_git_index(self.root, set(self.entries), ())
        self.assertTrue(checked)
        self.assertEqual(findings[0]["path"], "index:README.md")
        self.assertEqual(findings[0]["rule"], "phone")

    def test_git_index_blocks_force_tracked_private_file(self):
        responses = [
            subprocess.CompletedProcess([], 0, str(self.root).encode(), b""),
            subprocess.CompletedProcess([], 0, b"100644 fixture 0\tprivate/profile.md\x00", b""),
        ]
        with patch.object(release.shutil, "which", return_value="git"), patch.object(release.subprocess, "run", side_effect=responses):
            findings, checked = release.check_git_index(self.root, set(self.entries), ())
        self.assertEqual(findings[0]["rule"], "tracked_non_public_file")

    def test_no_git_dependency_is_required(self):
        with patch.object(release.shutil, "which", return_value=None):
            self.assertEqual(release.check_git_index(self.root, set(self.entries), ()), ([], False))

    def test_cli_pass_and_invalid_denylist(self):
        with redirect_stdout(io.StringIO()) as output:
            code = release.main(["--root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output.getvalue())["git_history_checked"])
        path = self.root / "private-denylist.json"
        path.write_text('[""]', encoding="utf-8")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(release.main(["--root", str(self.root), "--denylist", str(path)]), 2)

    def test_cli_refuses_export_when_scan_fails(self):
        (self.root / "unreviewed.md").write_text("fixture", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            code = release.main(["--root", str(self.root), "--export", "exports/demo.zip"])
        self.assertEqual(code, 1)
        self.assertFalse((self.root / "exports").exists())


if __name__ == "__main__":
    unittest.main()

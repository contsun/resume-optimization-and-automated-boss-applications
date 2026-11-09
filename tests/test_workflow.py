import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import workflow


def record(identifier="demo-a", **changes):
    result = dict.fromkeys(workflow.FIELDS, "")
    result.update(
        record_id=identifier,
        platform="demo-platform",
        employer_key="fictional-a",
        job_key="demo-job-a",
        recruiter_key="demo-person-a",
        role_family="service-product",
        location="demo-city",
        status="reviewed",
    )
    result.update(changes)
    return result


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def ledger(self, records):
        path = self.root / "applications.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=workflow.FIELDS)
            writer.writeheader()
            writer.writerows(records)
        return path

    def seed_templates(self):
        for relative in ["config/profile.example.json", "config/automation.example.json", *[f"templates/{name}" for name in (
            "resume.md", "facts.csv", "jd-review.md", "boss-profile.md", "outreach.md", "applications.csv", "intake.md", "session-plan.md",
            "application-preferences.md",
        )]]:
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / relative).read_bytes())

    def test_init_creates_only_private_offline_material(self):
        self.seed_templates()
        destination = workflow.initialize(self.root, "demo-search")
        self.assertEqual(destination, self.root / "private/demo-search")
        profile = json.loads((destination / "profile.json").read_text())
        self.assertFalse(profile["external_send_enabled"])
        self.assertFalse(profile["schedule_enabled"])
        automation = json.loads((destination / "automation.json").read_text())
        self.assertFalse(automation["enabled"])
        self.assertTrue(automation["dry_run"])
        self.assertEqual(profile["cities"], [])
        self.assertEqual(workflow.read_ledger(destination / "applications.csv"), [])
        self.assertTrue((destination / "intake.md").is_file())
        self.assertEqual((destination / "session-plan.md").read_bytes(), (ROOT / "templates/session-plan.md").read_bytes())

    def test_application_preferences_start_unconfirmed_without_attachment_permission(self):
        self.seed_templates()
        destination = workflow.initialize(self.root, "preferences-demo")
        profile = json.loads((destination / "profile.json").read_text())
        self.assertEqual(profile["target_roles"], [])
        self.assertEqual(profile["cities"], [])
        self.assertIsNone(profile["salary"]["matching_rule"])
        self.assertIsNone(profile["outreach"]["max_new_conversations"])
        self.assertEqual(profile["outreach"]["action"], "review_only")
        self.assertFalse(profile["outreach"]["send_attachment"])
        self.assertEqual((destination / "application-preferences.md").read_bytes(), (ROOT / "templates/application-preferences.md").read_bytes())

    def test_init_never_overwrites_existing_workspace(self):
        self.seed_templates()
        destination = workflow.initialize(self.root, "demo")
        original = (destination / "resume.md").read_bytes()
        with self.assertRaises(ValueError):
            workflow.initialize(self.root, "demo")
        self.assertEqual((destination / "resume.md").read_bytes(), original)

    def test_init_rejects_traversal_and_invalid_names(self):
        for name in ("../escape", "demo/child", "Demo", "", "demo space"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                workflow.initialize(self.root, name)

    def test_init_rejects_symlinked_private_directory(self):
        self.seed_templates()
        external = self.root / "external"
        external.mkdir()
        (self.root / "private").symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            workflow.initialize(self.root, "demo")
        self.assertEqual(list(external.iterdir()), [])

    def test_missing_template_does_not_create_partial_workspace(self):
        with self.assertRaises(OSError):
            workflow.initialize(self.root, "demo")
        self.assertFalse((self.root / "private").exists())

    def test_read_ledger_rejects_duplicate_identifiers(self):
        with self.assertRaises(ValueError):
            workflow.read_ledger(self.ledger([record(), record()]))

    def test_read_ledger_rejects_unsupported_status(self):
        with self.assertRaises(ValueError):
            workflow.read_ledger(self.ledger([record(status="unknown-state")]))

    def test_read_ledger_rejects_incomplete_header(self):
        path = self.root / "invalid.csv"
        path.write_text("record_id,status\ndemo,reviewed\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            workflow.read_ledger(path)

    def test_confirmed_event_requires_timezone_and_evidence(self):
        for changes in (
            {"greeting_verified_at": "2026-01-02T10:00:00", "evidence_ref": "fixture"},
            {"greeting_verified_at": "2026-01-02T10:00:00+08:00"},
            {"greeting_verified_at": "not-a-time", "evidence_ref": "fixture"},
            {"status": "greeting_verified"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                workflow.read_ledger(self.ledger([record(**changes)]))

    def test_examples_have_separate_verified_events(self):
        records = workflow.read_ledger(ROOT / "examples/fictional-applications.csv")
        report = workflow.summarize(records)
        self.assertEqual(report["verified_greetings"], 2)
        self.assertEqual(report["verified_resume_deliveries"], 1)
        self.assertEqual(report["replies"], 0)
        self.assertFalse(report["platform_verified"])
        self.assertFalse(workflow.deduplicate(records)["requires_manual_review"])

    def test_exact_job_duplicate_uses_platform(self):
        report = workflow.deduplicate([record(), record("demo-b")])
        self.assertEqual(report["exact_job_duplicates"], [["demo-a", "demo-b"]])
        cross_platform = workflow.deduplicate([record(), record("demo-b", platform="other-platform")])
        self.assertEqual(cross_platform["exact_job_duplicates"], [])
        self.assertTrue(cross_platform["suspected_vacancy_duplicates"])

    def test_recruiter_recontact_is_separate_from_vacancy_proof(self):
        report = workflow.deduplicate([
            record(), record("demo-b", job_key="other-job", role_family="other-role"),
        ])
        self.assertTrue(report["recruiter_recontacts"])
        self.assertEqual(report["suspected_vacancy_duplicates"], [])

    def test_normalization_is_conservative_and_case_insensitive(self):
        report = workflow.deduplicate([
            record(), record("demo-b", employer_key=" FICTIONAL-A ", job_key="different", recruiter_key="different"),
        ])
        self.assertTrue(report["suspected_vacancy_duplicates"])
        self.assertTrue(report["requires_manual_review"])

    def test_unresolved_outcome_and_missing_identity_block_review(self):
        report = workflow.deduplicate([record(status="pending_verification", employer_key="")])
        self.assertEqual(report["unresolved_record_ids"], ["demo-a"])
        self.assertEqual(report["incomplete_identity_record_ids"], ["demo-a"])
        self.assertTrue(report["requires_manual_review"])

    def test_skipped_unsent_record_is_not_send_history(self):
        report = workflow.deduplicate([record(), record("demo-b", status="skipped")])
        self.assertFalse(report["requires_manual_review"])

    def test_skipping_cannot_hide_previously_verified_send(self):
        report = workflow.deduplicate([
            record(), record("demo-b", status="skipped", greeting_verified_at="2026-01-02T10:00:00+08:00"),
        ])
        self.assertTrue(report["exact_job_duplicates"])

    def test_closed_state_keeps_confirmed_event_counts(self):
        records = workflow.read_ledger(self.ledger([
            record(status="closed", greeting_verified_at="2026-01-02T10:00:00+08:00", evidence_ref="fixture"),
        ]))
        report = workflow.summarize(records)
        self.assertEqual(report["verified_greetings"], 1)
        self.assertEqual(report["verified_resume_deliveries"], 0)

    def test_empty_summary_does_not_invent_successes(self):
        report = workflow.summarize([])
        self.assertEqual(report["record_count"], 0)
        self.assertEqual(report["verified_greetings"], 0)

    def test_cli_returns_nonzero_for_duplicate_review(self):
        path = self.ledger([record(), record("demo-b")])
        with redirect_stdout(io.StringIO()) as output:
            code = workflow.main(["dedupe", str(path)])
        self.assertEqual(code, 1)
        self.assertTrue(json.loads(output.getvalue())["requires_manual_review"])

    def test_cli_reports_bad_input_without_echoing_it(self):
        with redirect_stderr(io.StringIO()) as output:
            code = workflow.main(["summary", str(self.root / "missing.csv")])
        self.assertEqual(code, 2)
        self.assertNotIn(str(self.root), output.getvalue())


if __name__ == "__main__":
    unittest.main()

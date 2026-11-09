import copy
import csv
import io
import json
import random
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from contextlib import redirect_stderr
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import automation
from boss_browser import BrowserError


def candidate(identifier="a", **changes):
    result = {
        "job_key": f"/job_detail/demo-{identifier}.html", "title": "产品经理",
        "company": f"虚构企业-{identifier}", "location": "示例城", "salary": "15-20K",
        "recruiter": f"虚构招聘人-{identifier}", "text": "职位描述：设计虚构产品",
        "button": "立即沟通", "education": "本科", "address": "虚构地址",
    }
    result.update(changes)
    return result


class Clock:
    def __init__(self):
        self.value = datetime(2026, 1, 5, 11, 29, tzinfo=automation.zone("Asia/Shanghai"))

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += timedelta(seconds=seconds)


class Browser:
    def __init__(self, jobs, clock):
        self.jobs = jobs
        self.clock = clock
        self.clicks = []
        self.receipt_error = False
        self.account_error = False
        self.on_inspect = None

    def preflight(self):
        if self.account_error:
            raise BrowserError("Account changed")

    def cards(self):
        return self.jobs

    def inspect(self, card):
        if self.on_inspect:
            self.on_inspect()
        return dict(card)

    def click(self, job, deadline):
        self.clicks.append((job["job_key"], self.clock.now()))

    def verify(self, job):
        if self.receipt_error:
            raise BrowserError("Unknown result")
        return {"status": "greeting_verified", "job_key": job["job_key"], "evidence": "fictional receipt"}

    def dismiss_receipt(self):
        pass

    def expand(self):
        return False


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.configuration = json.loads((ROOT / "config/automation.example.json").read_text())
        self.configuration.update(enabled=True, dry_run=False)
        self.configuration["browser"].update(account_name="fictional-user", window_id=1, tab_id=2)
        self.configuration["limits"].update(per_batch=2, per_day=3)
        self.profile = json.loads((ROOT / "config/profile.example.json").read_text())
        self.profile.update(target_roles=["产品经理"], cities=["示例城"], resume_version="fictional-v1", external_send_enabled=True, schedule_enabled=True)
        self.profile["outreach"].update(action="first_greeting")
        self.profile["salary"].update(minimum=10000, maximum=30000, matching_rule="overlap")
        self.clock = Clock()
        self.browser = Browser([candidate("a"), candidate("b"), candidate("c"), candidate("d")], self.clock)
        self.save_settings()

    def save_settings(self):
        automation.save_json(self.workspace / "automation.json", self.configuration)
        automation.save_json(self.workspace / "profile.json", self.profile)

    def engine(self, authorize=True):
        engine = automation.Engine(self.workspace, self.browser, self.clock.now, self.clock.sleep)
        if authorize:
            automation.authorize(engine, self.clock.now().date() + timedelta(days=7))
        return engine

    def test_default_configuration_is_disabled_and_dry(self):
        default = json.loads((ROOT / "config/automation.example.json").read_text())
        self.assertFalse(default["enabled"])
        self.assertTrue(default["dry_run"])
        self.assertEqual(default["limits"]["per_batch"], 50)
        self.assertEqual(default["limits"]["per_day"], 100)
        self.assertEqual(default["windows"][0]["start"], "09:30")

    def test_random_plan_stays_in_window_and_weekdays(self):
        for seed in range(30):
            plan = automation.make_plan(self.configuration, date(2026, 1, 5), random.Random(seed))
            morning = datetime.fromisoformat(plan["morning"]["scheduled_at"])
            self.assertTrue((9, 30) <= (morning.hour, morning.minute) < (11, 30))
        self.configuration["weekdays"] = [6, 7]
        self.assertEqual(automation.make_plan(self.configuration, date(2026, 1, 5)), {})

    def test_invalid_and_overlapping_configuration_is_rejected(self):
        for field, value in (("per_batch", 0), ("per_day", True), ("minimum_interval_seconds", -1)):
            invalid = copy.deepcopy(self.configuration)
            invalid["limits"][field] = value
            with self.assertRaises(automation.AutomationError):
                automation.validate(invalid, self.profile)
        self.configuration["windows"][1]["start"] = "11:00"
        with self.assertRaises(automation.AutomationError):
            automation.validate(self.configuration, self.profile)

    def test_disabled_tick_has_no_browser_or_state_activity(self):
        self.configuration["enabled"] = False
        self.save_settings()
        result = self.engine(False).tick()
        self.assertEqual(result["status"], "disabled")
        self.assertFalse((self.workspace / "automation-state.json").exists())

    def test_no_authorization_cannot_send(self):
        with self.assertRaises(automation.AutomationError):
            self.engine(False).tick()
        self.assertFalse(self.browser.clicks)

    def test_authorization_requires_confirmed_scope(self):
        for field in ("external_send_enabled", "schedule_enabled"):
            original = self.profile[field]
            self.profile[field] = False
            self.save_settings()
            with self.assertRaises(automation.AutomationError):
                self.engine()
            self.profile[field] = original

    def test_plan_persists_and_is_not_redrawn(self):
        engine = self.engine()
        first = engine.plan(self.clock.now().date())
        self.assertEqual(first, engine.plan(self.clock.now().date()))
        self.configuration["limits"]["per_batch"] = 4
        self.save_settings()
        with self.assertRaises(automation.AutomationError):
            self.engine().plan(self.clock.now().date())

    def test_batch_quota_spacing_and_no_repeat(self):
        engine = self.engine()
        result = engine.tick()
        self.assertEqual(result["attempted"], 2)
        self.assertEqual(result["verified_greetings"], 2)
        self.assertGreaterEqual((self.browser.clicks[1][1] - self.browser.clicks[0][1]).total_seconds(), 31)
        self.assertEqual(engine.tick()["status"], "idle")
        self.assertEqual(len(self.browser.clicks), 2)

    def test_daily_quota_covers_both_windows(self):
        engine = self.engine()
        engine.tick()
        self.clock.value = self.clock.value.replace(hour=15, minute=59, second=0)
        result = engine.tick()
        self.assertEqual(result["attempted"], 1)
        self.assertEqual(len(self.browser.clicks), 3)

    def test_unknown_receipt_stops_all_future_batches_without_retry(self):
        engine = self.engine()
        self.browser.receipt_error = True
        self.assertEqual(engine.tick()["status"], "pending_verification")
        self.clock.value = self.clock.value.replace(hour=15, minute=59)
        with self.assertRaises(automation.AutomationError):
            engine.tick()
        self.assertEqual(len(self.browser.clicks), 1)

    def test_dry_run_does_not_send_or_modify_live_history(self):
        self.configuration["dry_run"] = True
        self.save_settings()
        result = self.engine(False).tick()
        self.assertEqual(result["planned"], 2)
        self.assertEqual(result["verified_greetings"], 0)
        self.assertFalse(self.browser.clicks)
        self.assertFalse((self.workspace / "automation-events.jsonl").exists())

    def test_account_mismatch_closes_batch_without_send(self):
        engine = self.engine()
        self.browser.account_error = True
        self.assertEqual(engine.tick()["status"], "error")
        self.assertFalse(self.browser.clicks)

    def test_profile_change_invalidates_authorization(self):
        engine = self.engine()
        self.profile["resume_version"] = "fictional-v2"
        self.save_settings()
        with self.assertRaises(automation.AutomationError):
            engine.tick()
        self.assertFalse(self.browser.clicks)

    def test_mid_batch_pause_stops_before_click(self):
        engine = self.engine()
        self.browser.on_inspect = lambda: automation.save_json(self.workspace / "automation-control.json", {"paused": True})
        self.assertEqual(engine.tick()["status"], "error")
        self.assertFalse(self.browser.clicks)

    def test_mid_batch_config_change_stops_before_click(self):
        engine = self.engine()
        def change():
            self.configuration["limits"]["per_day"] = 4
            self.save_settings()
        self.browser.on_inspect = change
        self.assertEqual(engine.tick()["status"], "error")
        self.assertFalse(self.browser.clicks)

    def test_missed_window_is_not_caught_up(self):
        self.clock.value = self.clock.value.replace(hour=12)
        engine = self.engine()
        self.assertEqual(engine.tick()["status"], "idle")
        self.assertEqual(engine.state()["days"]["2026-01-05"]["batches"]["morning"]["status"], "missed_window")
        self.assertFalse(self.browser.clicks)

    def test_timeout_is_checked_even_when_no_job_matches(self):
        engine = self.engine()
        self.configuration["limits"]["maximum_run_minutes"] = 1
        self.save_settings()
        engine = self.engine()
        self.browser.on_inspect = lambda: self.clock.sleep(61)
        self.browser.jobs = [candidate("a", title="无关职位"), candidate("b")]
        self.assertEqual(engine.tick()["status"], "timed_out")
        self.assertFalse(self.browser.clicks)

    def test_employer_alias_and_suspected_vacancy_are_deduplicated(self):
        self.configuration["matching"]["employer_aliases"] = {"虚构简称": "虚构全称"}
        self.save_settings()
        self.browser.jobs = [candidate("a", company="虚构全称"), candidate("b", company="虚构简称")]
        result = self.engine().tick()
        self.assertEqual(result["attempted"], 1)

    def test_salary_font_and_unknown_salary_do_not_pass(self):
        for salary in ("面议", "\ue031\ue032K", "100-200元/天", "20-10K"):
            matched, reason = automation.assess(candidate(salary=salary), self.profile, self.configuration, [])
            self.assertIsNone(matched)

    def test_salary_matching_rules_are_explicit(self):
        self.profile["salary"]["minimum"] = 18000
        matched, reason = automation.assess(candidate(), self.profile, self.configuration, [])
        self.assertIsNotNone(matched)
        self.profile["salary"]["matching_rule"] = "minimum_at_least"
        matched, reason = automation.assess(candidate(), self.profile, self.configuration, [])
        self.assertIsNone(matched)

    def test_hard_conditions_require_matching_manual_review(self):
        self.profile["hard_requirements"] = ["需人工核验的条件"]
        job = candidate()
        self.assertIsNone(automation.assess(job, self.profile, self.configuration, [])[0])
        review = {"approved": True, "fingerprint": automation.job_fingerprint(job), "resume_version": "fictional-v1", "reason": "虚构人工核验"}
        self.assertIsNotNone(automation.assess(job, self.profile, self.configuration, [review])[0])
        job["text"] += " changed"
        self.assertIsNone(automation.assess(job, self.profile, self.configuration, [review])[0])

    def test_demo_reports_plans_not_real_delivery(self):
        result = automation.demo()
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["planned"], 1)
        self.assertEqual(result["attempted"], 0)
        self.assertEqual(result["verified_greetings"], 0)

    def test_crashed_attempt_blocks_future_ticks(self):
        engine = self.engine()
        engine.record("attempt", attempt_id="fictional-attempt", candidate=candidate(), batch_key="old/morning")
        with self.assertRaises(automation.AutomationError):
            engine.tick()
        self.assertFalse(self.browser.clicks)

    def test_expired_authorization_cannot_run(self):
        engine = self.engine()
        self.clock.value += timedelta(days=9)
        with self.assertRaises(automation.AutomationError):
            engine.tick()
        self.assertFalse(self.browser.clicks)

    def test_changed_configuration_requires_new_authorization(self):
        self.engine()
        self.configuration["limits"]["per_batch"] = 3
        self.save_settings()
        with self.assertRaises(automation.AutomationError):
            self.engine(False).tick()

    def test_receipt_time_also_contributes_to_minimum_spacing(self):
        engine = self.engine()
        original_click = self.browser.click
        def delayed_click(job, deadline):
            if not self.browser.clicks:
                self.clock.sleep(20)
            original_click(job, deadline)
        self.browser.click = delayed_click
        engine.tick()
        self.assertGreaterEqual((self.browser.clicks[1][1] - self.browser.clicks[0][1]).total_seconds(), 31)

    def test_manual_pending_ledger_prevents_new_sends(self):
        record = dict.fromkeys(automation.workflow.FIELDS, "")
        record.update(record_id="fictional-pending", status="pending_verification")
        with (self.workspace / "applications.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=automation.workflow.FIELDS)
            writer.writeheader()
            writer.writerow(record)
        with self.assertRaises(automation.AutomationError):
            self.engine()
        self.assertFalse(self.browser.clicks)

    def test_manual_history_consumes_daily_budget(self):
        record = dict.fromkeys(automation.workflow.FIELDS, "")
        record.update(record_id="fictional-old", status="greeting_verified", greeting_verified_at=self.clock.now().isoformat(), evidence_ref="fictional-evidence", job_key="old-job", employer_key="old-employer")
        with (self.workspace / "applications.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=automation.workflow.FIELDS)
            writer.writeheader()
            writer.writerow(record)
        self.configuration["limits"]["per_day"] = 1
        self.save_settings()
        self.assertEqual(self.engine().tick()["status"], "quota_reached")
        self.assertFalse(self.browser.clicks)

    def test_changed_job_receipt_remains_pending(self):
        engine = self.engine()
        self.browser.verify = lambda job: {"status": "greeting_verified", "job_key": "wrong-job", "evidence": "fictional"}
        self.assertEqual(engine.tick()["status"], "pending_verification")
        self.assertEqual(len(self.browser.clicks), 1)

    def test_symlinked_event_log_is_not_followed(self):
        engine = self.engine()
        target = self.workspace / "untouched.txt"
        target.write_text("fixture")
        engine.events_path.symlink_to(target)
        with self.assertRaises(automation.AutomationError):
            engine.tick()
        self.assertEqual(target.read_text(), "fixture")

    def test_review_needed_preview_contains_fingerprint(self):
        self.configuration["dry_run"] = True
        self.profile["hard_requirements"] = ["需人工核验"]
        self.save_settings()
        engine = self.engine(False)
        engine.tick()
        reviews = [event for event in engine.events() if event["action"] == "review_required"]
        self.assertTrue(reviews)
        self.assertEqual(reviews[0]["fingerprint"], automation.job_fingerprint(self.browser.jobs[0]))
        self.assertFalse(self.browser.clicks)

    def test_pause_cli_does_not_wait_for_batch_lock(self):
        with patch.object(automation, "private_workspace", return_value=self.workspace), automation.workspace_lock(self.workspace):
            with patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(automation.main(["pause", "fixture"]), 0)
        self.assertTrue(automation.load_json(self.workspace / "automation-control.json")["paused"])

    def test_duplicate_lock_is_rejected(self):
        with automation.workspace_lock(self.workspace):
            with self.assertRaises(automation.AutomationError):
                with automation.workspace_lock(self.workspace):
                    self.fail("second lock acquired")

    def test_closed_batch_is_not_reopened_by_renewing_authorization(self):
        engine = self.engine()
        engine.tick()
        automation.authorize(engine, self.clock.now().date() + timedelta(days=1))
        self.assertEqual(engine.tick()["status"], "idle")
        self.assertEqual(len(self.browser.clicks), 2)

    def test_notification_command_failure_does_not_repeat_sends(self):
        self.configuration["notification"].update(enabled=True, command=["fictional-notifier"])
        self.save_settings()
        engine = self.engine()
        with patch("automation.subprocess.run", side_effect=OSError("fixture")):
            result = engine.tick()
        self.assertFalse(result["notification_command_succeeded"])
        self.assertEqual(engine.tick()["status"], "idle")
        self.assertEqual(len(self.browser.clicks), 2)

    def test_cli_invalid_workspace_does_not_expose_local_content(self):
        error = io.StringIO()
        with redirect_stderr(error):
            self.assertEqual(automation.main(["tick", "not-a-private-workspace"]), 2)
        self.assertNotIn(str(ROOT), error.getvalue())

    def test_existing_preference_cap_can_only_reduce_batch_size(self):
        self.profile["outreach"]["max_new_conversations"] = 1
        self.save_settings()
        self.assertEqual(self.engine().tick()["attempted"], 1)

    def test_runtime_never_sends_across_local_midnight(self):
        self.configuration["windows"] = [{"name": "late", "start": "23:00", "end": "23:59"}]
        self.configuration["limits"].update(per_batch=10, per_day=10)
        self.save_settings()
        self.clock.value = self.clock.value.replace(hour=23, minute=58, second=55)
        result = self.engine().tick()
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(len(self.browser.clicks), 3)
        self.assertEqual(automation.timestamp(result["deadline"]).date(), date(2026, 1, 6))
        self.assertTrue(all(moment.date() == date(2026, 1, 5) for job, moment in self.browser.clicks))

    def test_timestamp_supports_utc_z_on_minimum_python(self):
        self.assertEqual(automation.timestamp("2026-01-05T03:29:00Z").astimezone(automation.zone("Asia/Shanghai")), self.clock.now())
        with self.assertRaises(automation.AutomationError):
            automation.timestamp("2026-01-05T03:29:00")

    def test_resolving_unknown_outcome_appends_without_reopening(self):
        engine = self.engine()
        self.browser.receipt_error = True
        engine.tick()
        attempt = next(event for event in engine.events() if event["action"] == "attempt")
        with patch.object(automation, "private_workspace", return_value=self.workspace), patch("sys.stdout", new=io.StringIO()):
            result = automation.main(["resolve", "fixture", "--attempt-id", attempt["attempt_id"], "--outcome", "verified_no_send", "--evidence", "fictional verified evidence", "--confirm-reviewed"])
        self.assertEqual(result, 0)
        self.assertEqual(engine.state()["days"]["2026-01-05"]["batches"]["morning"]["status"], "pending_verification")
        self.assertEqual(len(engine.history()), 1)
        self.assertEqual(len(self.browser.clicks), 1)

    def test_notification_success_is_not_claimed_as_delivery(self):
        self.configuration["notification"].update(enabled=True, command=["fictional-notifier"])
        self.save_settings()
        engine = self.engine()
        with patch("automation.subprocess.run"):
            engine.tick()
        event = next(event for event in engine.events() if event["action"] == "notification")
        self.assertTrue(event["command_succeeded"])
        self.assertFalse(event["delivery_confirmed"])

    def test_demo_adapter_setting_cannot_touch_real_browser(self):
        self.configuration["dry_run"] = True
        self.configuration["browser"]["adapter"] = "demo"
        self.save_settings()
        engine = automation.Engine(self.workspace, clock=self.clock.now, sleep=self.clock.sleep)
        with patch("automation.ChromeBossBrowser", side_effect=AssertionError("real browser constructed")):
            self.assertEqual(engine.tick()["attempted"], 0)


if __name__ == "__main__":
    unittest.main()

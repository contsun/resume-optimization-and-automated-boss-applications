import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from boss_browser import BrowserError, ChromeBossBrowser, DemoBrowser, valid_job_path
from test_automation import Clock, candidate


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.browser = ChromeBossBrowser({"account_name": "fictional-user", "window_id": 1, "tab_id": 2}, sleep=lambda seconds: None)

    def test_external_links_parameters_and_traversal_are_rejected(self):
        for value in (None, "https://example.com/", "/job_detail/../demo.html", "/job_detail/demo.html?x=1", "javascript:alert(1)"):
            self.assertFalse(valid_job_path(value))
        self.assertTrue(valid_job_path("/job_detail/demo~a.html"))

    def test_preflight_rejects_account_dialog_and_restriction(self):
        for response in ({"account": False, "restricted": False, "dialogs": False}, {"account": True, "restricted": True, "dialogs": False}, {"account": True, "restricted": False, "dialogs": True}):
            with patch.object(self.browser, "evaluate", return_value=response):
                with self.assertRaises(BrowserError):
                    self.browser.preflight()

    def test_readable_success_requires_exact_job_title_and_key(self):
        job = candidate()
        response = {"job_key": job["job_key"], "header": "已向BOSS发送消息", "message": "产品经理", "restricted": False, "account": True}
        with patch.object(self.browser, "evaluate", return_value=response):
            self.assertEqual(self.browser.verify(job)["status"], "greeting_verified")
        response["job_key"] = "/job_detail/other.html"
        with patch.object(self.browser, "evaluate", return_value=response):
            with self.assertRaises(BrowserError):
                self.browser.verify(job)

    def test_unknown_receipt_only_reads_never_clicks(self):
        with patch.object(self.browser, "evaluate", return_value={"account": True}) as evaluate:
            with self.assertRaises(BrowserError):
                self.browser.verify(candidate())
            self.assertEqual(evaluate.call_count, 16)
            self.assertTrue(all("button.click()" not in call.args[0] for call in evaluate.call_args_list))

    def test_platform_restriction_stops_receipt_polling_immediately(self):
        with patch.object(self.browser, "evaluate", return_value={"restricted": True}) as evaluate:
            with self.assertRaises(BrowserError):
                self.browser.verify(candidate())
            self.assertEqual(evaluate.call_count, 1)

    def test_send_embeds_account_job_snapshot_and_deadline_guards(self):
        with patch.object(self.browser, "evaluate", return_value=True) as evaluate:
            self.browser.click(candidate(), Clock().now())
            source = evaluate.call_args.args[0]
            self.assertEqual(source.count("button.click()"), 1)
            self.assertIn("Date.now()>=deadline", source)
            self.assertIn("expected.recruiter", source)
            self.assertIn("root?.innerText!==expected.text", source)
            self.assertIn("fictional-user", source)

    def test_browser_transport_failure_is_not_retried(self):
        with patch("boss_browser.sys.platform", "darwin"), patch("boss_browser.subprocess.run", side_effect=subprocess.TimeoutExpired("fixture", 20)) as run:
            with self.assertRaises(BrowserError):
                self.browser.evaluate("return true;")
            self.assertEqual(run.call_count, 1)

    def test_browser_host_guard_and_json_transport(self):
        response = subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "data": True}), "")
        with patch("boss_browser.sys.platform", "darwin"), patch("boss_browser.subprocess.run", return_value=response) as run:
            self.assertTrue(self.browser.evaluate("return true;"))
            self.assertIn("www.zhipin.com", run.call_args.kwargs["input"])
            self.assertIn("/web/geek/jobs", run.call_args.kwargs["input"])

    def test_demo_cannot_send_even_if_called_directly(self):
        with self.assertRaises(BrowserError):
            DemoBrowser([]).click(candidate(), Clock().now())


if __name__ == "__main__":
    unittest.main()

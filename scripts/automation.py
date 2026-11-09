from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time as daytime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import workflow
from boss_browser import BrowserError, ChromeBossBrowser, DemoBrowser, valid_job_path


ROOT = Path(__file__).resolve().parents[1]


class AutomationError(RuntimeError):
    pass


def fingerprint(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AutomationError("事件时间必须包含时区")
    return parsed


def load_json(path: Path, default=None):
    if path.is_symlink():
        raise AutomationError("私有状态和配置不能是符号链接")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path: Path, value) -> None:
    if path.is_symlink():
        raise AutomationError("不能写入符号链接")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.replace(temporary, path)


@contextmanager
def workspace_lock(workspace: Path):
    path = workspace / "automation.lock"
    if path.is_symlink():
        raise AutomationError("锁文件不能是符号链接")
    with path.open("a+b") as stream:
        os.chmod(path, 0o600)
        if sys.platform == "win32":
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise AutomationError("同一私有工作区已有执行器；本次不重复运行") from error
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise AutomationError("同一私有工作区已有执行器；本次不重复运行") from error
        try:
            yield
        finally:
            if sys.platform == "win32":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def zone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name)
        raise AutomationError("本机缺少配置时区的数据；请安装系统时区数据后重试")


def minute(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise AutomationError("时间必须使用 24 小时 HH:MM 格式")
    hours, minutes = map(int, value.split(":"))
    return hours * 60 + minutes


def validate(configuration: dict, profile: dict, live: bool = False) -> None:
    if not isinstance(configuration, dict) or configuration.get("schema_version") != 1:
        raise AutomationError("自动化配置版本不受支持")
    for field in ("enabled", "dry_run"):
        if type(configuration.get(field)) is not bool:
            raise AutomationError("enabled 和 dry_run 必须显式填写布尔值")
    zone(configuration["timezone"])
    weekdays = configuration["weekdays"]
    if not weekdays or any(type(value) is not int or not 1 <= value <= 7 for value in weekdays):
        raise AutomationError("weekdays 使用 1 至 7 表示周一至周日")
    names = set()
    intervals = []
    if not configuration["windows"]:
        raise AutomationError("至少配置一个启动窗口")
    for window in configuration["windows"]:
        name = window["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name) or name in names:
            raise AutomationError("窗口名称须唯一，使用小写字母、数字和连字符")
        names.add(name)
        start, end = minute(window["start"]), minute(window["end"])
        if start >= end or any(start < previous_end and end > previous_start for previous_start, previous_end in intervals):
            raise AutomationError("启动窗口不能跨午夜、倒置或相互重叠")
        intervals.append((start, end))
    for field in ("per_batch", "per_day", "minimum_interval_seconds", "maximum_run_minutes", "maximum_candidates", "maximum_expansions"):
        value = configuration["limits"][field]
        if type(value) is not int or not 1 <= value <= 10000:
            raise AutomationError("数量、间隔和时限须为 1 至 10000 的整数")
    matching = configuration["matching"]
    for field in ("required_keywords", "excluded_keywords", "allowed_education"):
        if not isinstance(matching[field], list) or any(not isinstance(value, str) or not value.strip() for value in matching[field]):
            raise AutomationError("筛选关键词须为非空字符串数组")
    if any(type(matching[field]) is not bool for field in ("distinct_employers_per_batch", "require_manual_review")):
        raise AutomationError("去重与复核开关须为布尔值")
    aliases = matching["employer_aliases"]
    if not isinstance(aliases, dict) or any(not isinstance(key, str) or not isinstance(value, str) or not key.strip() or not value.strip() for key, value in aliases.items()):
        raise AutomationError("雇主别名须映射到非空的统一名称")
    notification = configuration["notification"]
    if type(notification["enabled"]) is not bool or not isinstance(notification["command"], list):
        raise AutomationError("通知配置无效")
    if any(not isinstance(value, str) or not value for value in notification["command"]) or (notification["enabled"] and not notification["command"]):
        raise AutomationError("通知命令须为可信本地程序的参数数组，不使用 shell 字符串")
    browser = configuration["browser"]
    if browser["adapter"] not in {"chrome_macos", "demo"} or (browser["adapter"] == "demo" and not configuration["dry_run"]):
        raise AutomationError("演示适配器只能用于 dry_run")
    if live:
        if not configuration["enabled"] or configuration["dry_run"]:
            raise AutomationError("真实执行需显式 enabled=true 且 dry_run=false")
        if not isinstance(browser.get("account_name"), str) or not browser["account_name"].strip():
            raise AutomationError("真实执行必须填写本人账号显示名")
        if any(type(browser.get(field)) is not int or browser[field] <= 0 for field in ("window_id", "tab_id")):
            raise AutomationError("真实执行需指定本人已登录的窗口和页签 ID")
        if profile.get("external_send_enabled") is not True or profile.get("schedule_enabled") is not True:
            raise AutomationError("profile.json 尚未允许首次沟通和定时执行")
        if not profile.get("resume_version") or profile.get("outreach", {}).get("action") != "first_greeting" or profile["outreach"].get("send_attachment") is not False:
            raise AutomationError("需确认参考简历版本及仅首次招呼、不发送附件的范围")
        if any(not isinstance(profile.get(field), list) or not profile[field] or any(not isinstance(value, str) or not value.strip() for value in profile[field]) for field in ("target_roles", "cities")):
            raise AutomationError("需确认目标岗位和城市，空白不代表不限")
        salary = profile["salary"]
        minimum, maximum = salary["minimum"], salary["maximum"]
        if salary["currency"] != "CNY" or salary["period"] != "monthly" or type(minimum) not in (int, float) or minimum <= 0:
            raise AutomationError("当前自动匹配只支持明确的人民币月薪最低值，单位元")
        if maximum is not None and (type(maximum) not in (int, float) or maximum < minimum):
            raise AutomationError("薪资上限不能低于下限")
        if salary["matching_rule"] not in {"overlap", "minimum_at_least", "minimum_within_range"}:
            raise AutomationError("需明确月薪范围的匹配规则")
        if salary["matching_rule"] == "minimum_within_range" and maximum is None:
            raise AutomationError("起薪在区间内的规则需要填写上限")
        cap = profile["outreach"].get("max_new_conversations")
        if cap is not None and (type(cap) is not int or cap <= 0):
            raise AutomationError("沟通数量须为正整数或使用自动化配置中的上限")


def make_plan(configuration: dict, day: date, generator=None) -> dict:
    generator = generator or random.SystemRandom()
    timezone_info = zone(configuration["timezone"])
    if day.isoweekday() not in configuration["weekdays"]:
        return {}
    midnight = datetime.combine(day, daytime(), timezone_info)
    return {
        window["name"]: {
            "scheduled_at": (midnight + timedelta(minutes=generator.randrange(minute(window["start"]), minute(window["end"])))).isoformat(),
            "window_end": (midnight + timedelta(minutes=minute(window["end"]))).isoformat(),
            "status": "scheduled",
        }
        for window in configuration["windows"]
    }


def job_fingerprint(candidate: dict) -> str:
    return fingerprint({field: candidate.get(field) for field in ("job_key", "title", "company", "location", "salary", "recruiter", "address", "education", "text")})


def assess(candidate: dict, profile: dict, configuration: dict, reviews: list[dict]) -> tuple[dict | None, str]:
    if not valid_job_path(candidate.get("job_key")) or any(not isinstance(candidate.get(field), str) or not candidate[field].strip() for field in ("title", "company", "location", "salary", "recruiter", "text")):
        return None, "incomplete_identity"
    if candidate.get("button") != "立即沟通" or re.search(r"某公司|某大型|某知名|保密", candidate["company"]):
        return None, "not_new_or_anonymous"
    normalize = workflow.normalize
    title, location, text = (normalize(candidate[field]) for field in ("title", "location", "text"))
    role = next((value for value in profile["target_roles"] if normalize(value) in title), None)
    city = next((value for value in profile["cities"] if normalize(value) in location), None)
    matching = configuration["matching"]
    aliases = {normalize(key): normalize(value) for key, value in matching["employer_aliases"].items()}
    employer = aliases.get(normalize(candidate["company"]), normalize(candidate["company"]))
    excluded = {aliases.get(normalize(value), normalize(value)) for value in profile.get("excluded_employers", [])}
    if not role or not city or employer in excluded or any(normalize(value) in title for value in profile.get("excluded_roles", [])):
        return None, "outside_scope"
    if any(normalize(value) not in text for value in matching["required_keywords"]) or any(normalize(value) in text for value in matching["excluded_keywords"]):
        return None, "keyword_mismatch"
    if matching["allowed_education"] and candidate.get("education") not in matching["allowed_education"]:
        return None, "education_requires_review"
    salary = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)K(?:[·•]\d{1,2}薪)?", re.sub(r"\s", "", candidate["salary"]).upper())
    if not salary:
        return None, "salary_unreadable_or_unsupported"
    lower, upper = (float(value) * 1000 for value in salary.groups())
    preferences = profile["salary"]
    minimum, maximum, rule = preferences["minimum"], preferences["maximum"], preferences["matching_rule"]
    if minimum is None or lower > upper or upper < minimum or (maximum is not None and lower > maximum):
        return None, "salary_mismatch"
    if rule in {"minimum_at_least", "minimum_within_range"} and lower < minimum:
        return None, "salary_mismatch"
    needs_review = matching["require_manual_review"] or any(profile.get(field) for field in ("hard_requirements", "target_levels", "work_modes"))
    approved = next((review for review in reviews if review.get("fingerprint") == job_fingerprint(candidate) and review.get("resume_version") == profile["resume_version"] and review.get("reason") and review.get("approved") is True), None)
    if needs_review and not approved:
        return None, "manual_review_required"
    result = dict(candidate)
    result.update(employer_key=employer, recruiter_key=normalize(candidate["recruiter"]), role_family=normalize(role), city=normalize(city))
    return result, "matched"


class Engine:
    def __init__(self, workspace: Path, adapter=None, clock=None, sleep=time.sleep):
        self.workspace = workspace
        self.configuration = load_json(workspace / "automation.json")
        self.profile = load_json(workspace / "profile.json")
        validate(self.configuration, self.profile)
        self.digest = fingerprint([self.configuration, self.profile])
        self.simulation = self.configuration["dry_run"]
        self.prefix = "simulation" if self.simulation else "automation"
        self.state_path = workspace / f"{self.prefix}-state.json"
        self.events_path = workspace / f"{self.prefix}-events.jsonl"
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self.adapter = adapter
        self.timezone = zone(self.configuration["timezone"])

    def now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise AutomationError("执行时钟必须包含时区")
        return value.astimezone(self.timezone)

    def state(self) -> dict:
        return load_json(self.state_path, {"schema_version": 1, "paused": False, "days": {}})

    def events(self) -> list[dict]:
        if self.events_path.is_symlink():
            raise AutomationError("事件台账不能是符号链接")
        if not self.events_path.exists():
            return []
        events = [json.loads(line) for line in self.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for event in events:
            if not isinstance(event, dict) or not event.get("action") or timestamp(event["at"]).tzinfo is None:
                raise AutomationError("事件台账损坏，停止而非忽略历史")
        return events

    def record(self, action: str, **fields) -> None:
        if self.events_path.is_symlink():
            raise AutomationError("不能写入符号链接台账")
        with self.events_path.open("a", encoding="utf-8") as stream:
            os.chmod(self.events_path, 0o600)
            stream.write(json.dumps({"action": action, "at": self.now().isoformat(), **fields}, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def history(self) -> list[dict]:
        events = self.events()
        settled = {event["attempt_id"] for event in events if event["action"] == "receipt" and event.get("status") in {"greeting_verified", "verified_no_send"} and event.get("evidence")}
        attempts = [event for event in events if event["action"] in {"attempt", "preview"}]
        if any(event["action"] == "attempt" and event["attempt_id"] not in settled for event in attempts):
            raise AutomationError("存在待核实发送，所有后续批次停止；不能重复点击")
        ledger = self.workspace / "applications.csv"
        if ledger.is_symlink():
            raise AutomationError("人工台账不能是符号链接")
        if ledger.exists():
            aliases = {workflow.normalize(key): workflow.normalize(value) for key, value in self.configuration["matching"]["employer_aliases"].items()}
            for record in workflow.read_ledger(ledger):
                if record["status"] == "pending_verification":
                    raise AutomationError("人工台账存在待核实项；先核验，不自动投递")
                timestamps = [record[field] for field in workflow.EVENT_FIELDS if record[field]]
                if timestamps and not any(event["candidate"]["job_key"] == record["job_key"] for event in attempts):
                    candidate = dict(record)
                    for field in ("employer_key", "recruiter_key", "role_family"):
                        candidate[field] = workflow.normalize(candidate[field])
                    candidate["employer_key"] = aliases.get(candidate["employer_key"], candidate["employer_key"])
                    candidate["city"] = workflow.normalize(record["location"])
                    attempts.append({"action": "manual", "at": min(timestamps, key=timestamp), "candidate": candidate})
        return attempts

    def check_authorization(self) -> None:
        current = [load_json(self.workspace / "automation.json"), load_json(self.workspace / "profile.json")]
        if fingerprint(current) != self.digest:
            raise AutomationError("配置或简历版本已变更；停止并重新确认授权")
        if self.state().get("paused") or load_json(self.workspace / "automation-control.json", {}).get("paused"):
            raise AutomationError("当前工作区已暂停")
        if not self.simulation:
            validate(self.configuration, self.profile, live=True)
            authorization = load_json(self.workspace / "authorization.json", {})
            if authorization.get("fingerprint") != self.digest or authorization.get("action") != "first_greeting":
                raise AutomationError("尚无与当前账号、配置和简历版本一致的明确授权")
            expires = timestamp(authorization["expires_at"])
            if expires.tzinfo is None or self.now() >= expires:
                raise AutomationError("定时首次沟通授权已过期")

    def plan(self, day: date) -> dict:
        state = self.state()
        key = day.isoformat()
        schedule_digest = fingerprint(self.configuration)
        if key not in state["days"]:
            state["days"][key] = {"fingerprint": schedule_digest, "batches": make_plan(self.configuration, day)}
            save_json(self.state_path, state)
        elif state["days"][key]["fingerprint"] != schedule_digest:
            raise AutomationError("当天计划已固定；新自动化配置从未生成计划的日期生效，不重抽或重开旧批次")
        return state["days"][key]["batches"]

    def duplicate(self, candidate: dict, history: list[dict], batch_key: str) -> bool:
        for event in history:
            previous = event["candidate"]
            if previous["job_key"] == candidate["job_key"]:
                return True
            if previous.get("employer_key") == candidate["employer_key"]:
                if previous.get("recruiter_key") == candidate["recruiter_key"]:
                    return True
                if previous.get("role_family") == candidate["role_family"] and previous.get("city") == candidate["city"]:
                    return True
                if self.configuration["matching"]["distinct_employers_per_batch"] and event.get("batch_key") == batch_key:
                    return True
        return False

    def execute(self, day: str, slot: str) -> dict:
        state = self.state()
        batch = state["days"][day]["batches"][slot]
        batch_key = f"{day}/{slot}"
        start = self.now()
        midnight = datetime.combine(start.date() + timedelta(days=1), daytime(), self.timezone)
        deadline = min(start + timedelta(minutes=self.configuration["limits"]["maximum_run_minutes"]), midnight)
        batch.update(status="running", started_at=start.isoformat(), deadline=deadline.isoformat())
        save_json(self.state_path, state)
        browser = self.adapter
        if browser is None:
            browser = DemoBrowser([]) if self.configuration["browser"]["adapter"] == "demo" else ChromeBossBrowser(self.configuration["browser"], self.sleep)
        reviews = load_json(self.workspace / "reviews.json", [])
        if not isinstance(reviews, list):
            raise AutomationError("reviews.json 须为人工审核记录数组")
        limits = self.configuration["limits"]
        cap = min(limits["per_batch"], self.profile["outreach"].get("max_new_conversations") or limits["per_batch"])
        seen = set()
        checked = 0
        attempts = 0
        verified = 0
        planned = 0
        outcome = "insufficient_matches"
        stop_reason = None
        try:
            for expansion in range(limits["maximum_expansions"] + 1):
                self.check_authorization()
                history = self.history()
                daily = sum(timestamp(event["at"]).astimezone(self.timezone).date() == start.date() for event in history)
                if attempts + planned >= cap or daily >= limits["per_day"]:
                    outcome = "quota_reached"
                    break
                if self.now() >= deadline:
                    outcome = "timed_out"
                    break
                browser.preflight()
                for card in browser.cards():
                    self.check_authorization()
                    if attempts + planned >= cap:
                        outcome = "quota_reached"
                        break
                    if self.now() >= deadline:
                        outcome = "timed_out"
                        break
                    job_key = card.get("job_key")
                    if job_key in seen:
                        continue
                    if checked >= limits["maximum_candidates"]:
                        break
                    seen.add(job_key)
                    checked += 1
                    observed = browser.inspect(card)
                    candidate, reason = assess(observed, self.profile, self.configuration, reviews)
                    if not candidate:
                        if reason == "manual_review_required":
                            self.record("review_required", candidate=observed, fingerprint=job_fingerprint(observed), reason=reason)
                        else:
                            self.record("skip", job_key=job_key, reason=reason)
                        continue
                    history = self.history()
                    if self.duplicate(candidate, history, batch_key):
                        self.record("skip", job_key=job_key, reason="historical_or_suspected_duplicate")
                        continue
                    self.check_authorization()
                    daily = sum(timestamp(event["at"]).astimezone(self.timezone).date() == start.date() for event in history)
                    if attempts + planned >= cap or daily >= limits["per_day"]:
                        outcome = "quota_reached"
                        break
                    if self.now() >= deadline:
                        outcome = "timed_out"
                        break
                    if self.simulation:
                        self.record("preview", candidate=candidate, batch_key=batch_key, fingerprint=job_fingerprint(candidate))
                        planned += 1
                        continue
                    if history:
                        spacing_events = history + [event for event in self.events() if event["action"] == "receipt"]
                        latest = max(timestamp(event["at"]) for event in spacing_events)
                        delay = max(0, limits["minimum_interval_seconds"] - (self.now() - latest).total_seconds())
                        if self.now() + timedelta(seconds=delay) >= deadline:
                            outcome = "timed_out"
                            break
                        if delay:
                            self.sleep(delay)
                    self.check_authorization()
                    browser.preflight()
                    self.check_authorization()
                    if self.now() >= deadline:
                        outcome = "timed_out"
                        break
                    attempt_id = uuid.uuid4().hex
                    self.record("attempt", attempt_id=attempt_id, candidate=candidate, batch_key=batch_key)
                    attempts += 1
                    browser.click(candidate, deadline)
                    receipt = browser.verify(candidate)
                    if receipt.get("status") != "greeting_verified" or receipt.get("job_key") != candidate["job_key"] or not receipt.get("evidence"):
                        raise BrowserError("回执无法证明同岗首次招呼")
                    self.record("receipt", attempt_id=attempt_id, **receipt)
                    verified += 1
                    browser.dismiss_receipt()
                if outcome in {"quota_reached", "timed_out"}:
                    break
                if attempts + planned >= cap:
                    outcome = "quota_reached"
                    break
                if checked >= limits["maximum_candidates"] or expansion == limits["maximum_expansions"] or not browser.expand():
                    break
                self.sleep(0.5)
        except (AutomationError, BrowserError, OSError, ValueError, KeyError, TypeError) as error:
            outcome = "pending_verification" if attempts > verified else "error"
            stop_reason = str(error) if isinstance(error, (AutomationError, BrowserError)) else "私有资料、状态或页面数据无效；停止而非忽略错误"
        batch.update(status=outcome, finished_at=self.now().isoformat(), attempted=attempts, verified_greetings=verified, planned=planned, checked=checked)
        if stop_reason:
            batch["stop_reason"] = stop_reason
        save_json(self.state_path, state)
        summary = {"batch": batch_key, **batch, "dry_run": self.simulation, "attachment_deliveries": 0}
        self.record("batch_closed", summary=summary)
        notification = self.configuration["notification"]
        if notification["enabled"] and not self.simulation:
            try:
                subprocess.run(notification["command"], input=json.dumps(summary, ensure_ascii=False), text=True, capture_output=True, timeout=20, check=True, shell=False)
                self.record("notification", command_succeeded=True, delivery_confirmed=False, batch_key=batch_key)
            except (OSError, subprocess.SubprocessError):
                self.record("notification", command_succeeded=False, delivery_confirmed=False, batch_key=batch_key)
                summary["notification_command_succeeded"] = False
        return summary

    def tick(self) -> dict:
        with workspace_lock(self.workspace):
            if not self.configuration["enabled"]:
                return {"status": "disabled", "external_actions": 0}
            self.check_authorization()
            self.history()
            today = self.now().date()
            batches = self.plan(today)
            for slot, batch in sorted(batches.items(), key=lambda item: item[1]["scheduled_at"]):
                if batch["status"] != "scheduled":
                    continue
                now = self.now()
                if now >= timestamp(batch["window_end"]):
                    state = self.state()
                    state["days"][today.isoformat()]["batches"][slot]["status"] = "missed_window"
                    save_json(self.state_path, state)
                    continue
                if now >= timestamp(batch["scheduled_at"]):
                    return self.execute(today.isoformat(), slot)
            return {"status": "idle", "date": today.isoformat(), "external_actions": 0}


def authorize(engine: Engine, until: date) -> dict:
    validate(engine.configuration, engine.profile, live=True)
    if until < engine.now().date() or until > engine.now().date() + timedelta(days=365):
        raise AutomationError("授权截止日期须在今天至未来一年内")
    engine.history()
    authorization = {"action": "first_greeting", "fingerprint": engine.digest, "confirmed_at": engine.now().isoformat(), "expires_at": datetime.combine(until + timedelta(days=1), daytime(), engine.timezone).isoformat()}
    save_json(engine.workspace / "authorization.json", authorization)
    state = engine.state()
    state["paused"] = False
    save_json(engine.state_path, state)
    save_json(engine.workspace / "automation-control.json", {"paused": False})
    return {"authorized": True, "expires_at": authorization["expires_at"], "external_actions": 0}


def demo() -> dict:
    configuration = load_json(ROOT / "config/automation.example.json")
    configuration.update(enabled=True)
    configuration["browser"]["adapter"] = "demo"
    profile = load_json(ROOT / "config/profile.example.json")
    profile.update(target_roles=["产品经理"], cities=["示例城"], resume_version="fictional-v1")
    profile["salary"].update(minimum=10000, maximum=30000, matching_rule="overlap")
    candidate = {"job_key": "/job_detail/demo-a.html", "title": "产品经理", "company": "虚构甲企业", "location": "示例城", "salary": "15-20K", "recruiter": "虚构招聘人", "text": "职位描述：管理虚构产品", "button": "立即沟通"}
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory)
        save_json(workspace / "automation.json", configuration)
        save_json(workspace / "profile.json", profile)
        engine = Engine(workspace, DemoBrowser([candidate]), clock=lambda: datetime(2026, 1, 5, 11, 29, tzinfo=zone("Asia/Shanghai")))
        return engine.tick()


def private_workspace(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    private = ROOT / "private"
    if private.is_symlink() or path.is_symlink() or path.parent.resolve() != private.resolve() or not path.is_dir():
        raise AutomationError("只能操作本项目 private 下已有的直属私有工作区")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="可配置的定时首次沟通；默认关闭真实发送，不发送附件")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="纯虚构、零发送演示，不读取浏览器")
    for command in ("validate", "plan", "status", "tick", "serve", "authorize", "pause", "resolve"):
        subparser = commands.add_parser(command)
        subparser.add_argument("workspace")
        if command == "plan":
            subparser.add_argument("--date", type=date.fromisoformat)
        elif command == "authorize":
            subparser.add_argument("--until", required=True, type=date.fromisoformat)
            subparser.add_argument("--confirm-first-greeting", action="store_true", required=True)
        elif command == "resolve":
            subparser.add_argument("--attempt-id", required=True)
            subparser.add_argument("--outcome", required=True, choices=("greeting_verified", "verified_no_send"))
            subparser.add_argument("--evidence", required=True)
            subparser.add_argument("--confirm-reviewed", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            result = demo()
        elif args.command == "pause":
            workspace = private_workspace(args.workspace)
            save_json(workspace / "automation-control.json", {"paused": True})
            result = {"paused": True, "external_actions": 0}
        else:
            engine = Engine(private_workspace(args.workspace))
            if args.command in {"tick", "serve"}:
                while True:
                    result = engine.tick()
                    if args.command == "tick":
                        break
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    time.sleep(60)
                    engine = Engine(engine.workspace)
            else:
                with workspace_lock(engine.workspace):
                    if args.command == "validate":
                        validate(engine.configuration, engine.profile, live=not engine.simulation)
                        result = {"valid": True, "dry_run": engine.simulation, "external_actions": 0}
                    elif args.command == "plan":
                        result = {"plan": engine.plan(args.date or engine.now().date()), "external_actions": 0}
                    elif args.command == "status":
                        result = {"state": engine.state(), "dry_run": engine.simulation, "external_actions": 0}
                    elif args.command == "authorize":
                        result = authorize(engine, args.until)
                    else:
                        attempts = [event for event in engine.events() if event["action"] == "attempt" and event["attempt_id"] == args.attempt_id]
                        if len(attempts) != 1 or not args.evidence.strip() or any(event["action"] == "receipt" and event["attempt_id"] == args.attempt_id for event in engine.events()):
                            raise AutomationError("只能对一个尚无结论的真实尝试追加经本人核验的结果")
                        engine.record("receipt", attempt_id=args.attempt_id, status=args.outcome, evidence=args.evidence, job_key=attempts[0]["candidate"]["job_key"], source="manual_verification")
                        result = {"resolved": True, "closed_batches_reopened": False, "external_actions": 0}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(result.get("status") in {"error", "pending_verification"})
    except KeyboardInterrupt:
        print("执行器已停止；已记录但未核实的尝试不会自动重试。", file=sys.stderr)
        return 130
    except (AutomationError, BrowserError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError):
        print("自动化未继续：检查配置、授权、暂停状态、计划及待核实台账；不会自动重试发送。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

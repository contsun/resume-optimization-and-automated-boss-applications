from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIELDS = (
    "record_id", "platform", "employer_key", "job_key", "recruiter_key",
    "role_family", "location", "status", "resume_version",
    "greeting_verified_at", "resume_sent_verified_at", "replied_at",
    "interview_at", "evidence_ref", "next_action", "notes",
)
STATUSES = {
    "shortlisted", "reviewed", "greeting_verified", "resume_sent_verified",
    "replied", "interviewing", "offer", "rejected", "closed",
    "pending_verification", "skipped",
}
EVENT_FIELDS = (
    "greeting_verified_at", "resume_sent_verified_at", "replied_at", "interview_at",
)
STATUS_EVENTS = {
    "greeting_verified": "greeting_verified_at",
    "resume_sent_verified": "resume_sent_verified_at",
    "replied": "replied_at",
    "interviewing": "interview_at",
}


def initialize(root: Path, name: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", name):
        raise ValueError("工作区名称只能使用小写字母、数字和连字符，且以字母开头")
    private_root = root / "private"
    if private_root.is_symlink():
        raise ValueError("private 不能是符号链接")
    destination = private_root / name
    if destination.exists() or destination.is_symlink():
        raise ValueError("目标工作区已存在，不覆盖任何资料")
    sources = {
        "profile.json": root / "config/profile.example.json",
        "automation.json": root / "config/automation.example.json",
        **{
            filename: root / "templates" / filename
            for filename in (
                "resume.md", "facts.csv", "jd-review.md", "boss-profile.md",
                "outreach.md", "applications.csv", "intake.md", "session-plan.md",
                "application-preferences.md",
            )
        },
    }
    contents = {name: path.read_bytes() for name, path in sources.items()}
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    for filename, content in contents.items():
        with (destination / filename).open("xb") as stream:
            stream.write(content)
    return destination


def read_ledger(path: Path) -> list[dict[str, str]]:
    records = []
    identifiers = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        if not set(FIELDS).issubset(headers) or len(headers) != len(set(headers)):
            raise ValueError("台账表头不完整或重复，请使用 applications.csv 模板")
        for line_number, raw in enumerate(reader, start=2):
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError(f"台账第 {line_number} 行列数与表头不一致")
            record = {field: raw[field].strip() for field in FIELDS}
            if not any(record.values()):
                continue
            identifier = record["record_id"]
            if not identifier or identifier in identifiers:
                raise ValueError(f"台账第 {line_number} 行记录编号为空或重复")
            identifiers.add(identifier)
            if record["status"] not in STATUSES:
                raise ValueError(f"台账第 {line_number} 行状态不受支持")
            for field in EVENT_FIELDS:
                if record[field]:
                    try:
                        timestamp = datetime.fromisoformat(record[field].replace("Z", "+00:00"))
                    except ValueError as error:
                        raise ValueError(f"台账第 {line_number} 行事件时间格式错误") from error
                    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                        raise ValueError(f"台账第 {line_number} 行事件时间必须包含时区")
                    if not record["evidence_ref"]:
                        raise ValueError(f"台账第 {line_number} 行已确认事件缺少证据引用")
            required_event = STATUS_EVENTS.get(record["status"])
            if required_event and not record[required_event]:
                raise ValueError(f"台账第 {line_number} 行状态缺少对应的已确认事件时间")
            records.append(record)
    return records


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def duplicate_groups(records: list[dict[str, str]], fields: tuple[str, ...]) -> list[list[str]]:
    groups = defaultdict(list)
    for record in records:
        key = tuple(normalize(record[field]) for field in fields)
        if all(key):
            groups[key].append(record["record_id"])
    return [identifiers for identifiers in groups.values() if len(identifiers) > 1]


def deduplicate(records: list[dict[str, str]]) -> dict:
    active = [
        record for record in records
        if record["status"] != "skipped" or any(record[field] for field in EVENT_FIELDS)
    ]
    identity_fields = ("platform", "employer_key", "job_key", "recruiter_key", "role_family", "location")
    report = {
        "exact_job_duplicates": duplicate_groups(active, ("platform", "job_key")),
        "recruiter_recontacts": duplicate_groups(active, ("employer_key", "recruiter_key")),
        "suspected_vacancy_duplicates": duplicate_groups(active, ("employer_key", "role_family", "location")),
        "unresolved_record_ids": [record["record_id"] for record in records if record["status"] == "pending_verification"],
        "incomplete_identity_record_ids": [record["record_id"] for record in active if any(not record[field] for field in identity_fields)],
    }
    report["requires_manual_review"] = any(report.values())
    report["platform_verified"] = False
    return report


def summarize(records: list[dict[str, str]]) -> dict:
    return {
        "record_count": len(records),
        "status_counts": dict(sorted(Counter(record["status"] for record in records).items())),
        "verified_greetings": sum(bool(record["greeting_verified_at"]) for record in records),
        "verified_resume_deliveries": sum(bool(record["resume_sent_verified_at"]) for record in records),
        "replies": sum(bool(record["replied_at"]) for record in records),
        "interviews": sum(bool(record["interview_at"]) for record in records),
        "pending_verification": sum(record["status"] == "pending_verification" for record in records),
        "source": "manual_local_ledger",
        "platform_verified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="仅处理本地资料，不连接或操作招聘平台")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="创建不覆盖已有资料的私有工作区").add_argument("name")
    for command in ("dedupe", "summary"):
        commands.add_parser(command).add_argument("ledger", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            destination = initialize(PROJECT_ROOT, args.name)
            result = {"created": destination.relative_to(PROJECT_ROOT).as_posix(), "external_actions": 0}
        else:
            records = read_ledger(args.ledger)
            result = deduplicate(records) if args.command == "dedupe" else summarize(records)
    except (OSError, UnicodeError, ValueError, csv.Error):
        print("操作失败：请检查路径、文件权限、工作区是否已存在及台账字段/时间/证据；未连接平台。", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result.get("requires_manual_review", False))


if __name__ == "__main__":
    raise SystemExit(main())

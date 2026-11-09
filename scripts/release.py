from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "config/public-files.json"
ARCHIVE_ROOT = "resume-optimization-and-automated-boss-applications"
IGNORED_ROOTS = {"private", "exports", ".git"}
IGNORED_COMPONENTS = {"__pycache__", ".pytest_cache"}
FORBIDDEN_COMPONENTS = IGNORED_ROOTS | {".cc-connect", "attachments", "runs", "logs", "node_modules"}
TEXT_SUFFIXES = {".md", ".py", ".json", ".csv", ".yml", ".yaml", ".txt"}
MAX_PUBLIC_BYTES = 1024 * 1024
PATTERNS = {
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "user_home_path": re.compile(r"/(?:Users|home)/[^/\s\"'`]+"),
    "windows_home_path": re.compile(r"[A-Za-z]:" + r"\\Users\\[^\s\\]+"),
    "live_job_link": re.compile(r"job_detail/[A-Za-z0-9_~\-]{8,}"),
    "platform_identity": re.compile(r"\b(?:ou|oc|on|cli)_[A-Za-z0-9]{8,}\b"),
    "cloud_credential": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "assigned_credential": re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)\s*[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_+/=.\-]{16,}"),
}
EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")
EXAMPLE_DOMAINS = {"example.com", "example.org", "example.net", "example.invalid"}


def finding(path: str, rule: str, line: int | None = None) -> dict:
    result = {"path": path, "rule": rule}
    if line is not None:
        result["line"] = line
    return result


def valid_public_path(name: str) -> bool:
    if not isinstance(name, str) or not name or "\\" in name:
        return False
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and path.as_posix() == name
        and not any(part in FORBIDDEN_COMPONENTS | {"..", "."} for part in path.parts)
        and (path.suffix in TEXT_SUFFIXES or name in {"LICENSE", ".gitignore"})
    )


def scan_content(name: str, content: bytes, deny_terms: tuple[str, ...] = ()) -> list[dict]:
    if len(content) > MAX_PUBLIC_BYTES:
        return [finding(name, "oversized_public_file")]
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        return [finding(name, "non_utf8_content")]
    if "\x00" in text:
        return [finding(name, "binary_content")]
    findings = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in PATTERNS.items():
            if pattern.search(line):
                findings.append(finding(name, rule, line_number))
        if any(match.group(1).casefold() not in EXAMPLE_DOMAINS for match in EMAIL.finditer(line)):
            findings.append(finding(name, "non_example_email", line_number))
        if any(term.casefold() in line.casefold() for term in deny_terms):
            findings.append(finding(name, "private_deny_term", line_number))
    return findings


def read_manifest(root: Path) -> list[str]:
    manifest_path = root / MANIFEST
    if manifest_path.is_symlink() or manifest_path.parent.is_symlink():
        raise ValueError("公开白名单不能经过符号链接")
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries or any(not valid_public_path(entry) for entry in entries):
        raise ValueError("公开白名单必须是安全的文本文件相对路径列表")
    if len(entries) != len(set(entries)) or MANIFEST not in entries:
        raise ValueError("公开白名单不能重复且必须包含自身")
    return entries


def inventory(root: Path) -> tuple[set[str], list[dict]]:
    paths = set()
    findings = []
    for directory, child_directories, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for child in list(child_directories):
            child_path = directory_path / child
            relative = child_path.relative_to(root)
            if child in IGNORED_COMPONENTS or relative.as_posix() in IGNORED_ROOTS:
                child_directories.remove(child)
            elif child_path.is_symlink():
                findings.append(finding(relative.as_posix(), "symlink"))
                child_directories.remove(child)
        for filename in filenames:
            path = directory_path / filename
            relative = path.relative_to(root).as_posix()
            if relative in IGNORED_ROOTS:
                continue
            if path.is_symlink():
                findings.append(finding(relative, "symlink"))
            else:
                paths.add(relative)
    return paths, findings


def check_git_index(root: Path, allowed: set[str], deny_terms: tuple[str, ...]) -> tuple[list[dict], bool]:
    if shutil.which("git") is None:
        return [], False
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True)
    if top.returncode or Path(os.fsdecode(top.stdout).strip()).resolve() != root:
        return [], False
    index = subprocess.run(["git", "ls-files", "--stage", "-z"], cwd=root, capture_output=True)
    if index.returncode:
        return [finding(".git/index", "git_index_unreadable")], True
    findings = []
    for entry in index.stdout.split(b"\x00"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", maxsplit=1)
        name = os.fsdecode(raw_name)
        mode, object_id, stage = metadata.split()
        if name not in allowed:
            findings.append(finding(name, "tracked_non_public_file"))
            continue
        if mode not in {b"100644", b"100755"} or stage != b"0":
            findings.append(finding(name, "unsafe_index_entry"))
            continue
        indexed = subprocess.run(["git", "show", f":{name}"], cwd=root, capture_output=True)
        if indexed.returncode:
            findings.append(finding(name, "git_index_content_unreadable"))
        else:
            findings.extend(scan_content(f"index:{name}", indexed.stdout, deny_terms))
    return findings, True


def inspect_project(root: Path, deny_terms: tuple[str, ...] = ()) -> tuple[dict[str, bytes], list[dict], bool]:
    allowed = set(read_manifest(root))
    observed, findings = inventory(root)
    for name in sorted(observed - allowed):
        findings.append(finding(name, "unexpected_public_file"))
    contents = {}
    for name in sorted(allowed):
        path = root / name
        if name not in observed:
            findings.append(finding(name, "missing_or_unsafe_public_file"))
            continue
        try:
            content = path.read_bytes()
        except OSError:
            findings.append(finding(name, "unreadable_public_file"))
            continue
        contents[name] = content
        findings.extend(scan_content(name, content, deny_terms))
    index_findings, index_checked = check_git_index(root, allowed, deny_terms)
    return contents, findings + index_findings, index_checked


def write_export(root: Path, contents: dict[str, bytes], destination: Path) -> Path:
    exports_root = root / "exports"
    if exports_root.is_symlink():
        raise ValueError("导出目录不能是符号链接")
    if not destination.is_absolute():
        destination = root / destination
    if destination.parent.resolve() != exports_root.resolve() or destination.suffix != ".zip":
        raise ValueError("只能导出到本项目 exports 目录下的 zip 文件")
    if destination.exists() or destination.is_symlink():
        raise ValueError("导出文件已存在，不覆盖")
    if any(not valid_public_path(name) for name in contents):
        raise ValueError("导出内容存在不安全路径")
    exports_root.mkdir(exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(contents.items()):
            info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{name}", date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return destination


def read_denylist(path: Path | None) -> tuple[str, ...]:
    if path is None:
        return ()
    terms = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(terms, list) or any(not isinstance(term, str) or len(term.strip()) < 2 for term in terms):
        raise ValueError("私有拒绝词表必须是长度至少为 2 的非空字符串数组")
    return tuple(term.strip() for term in terms)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线检查并按精确白名单导出；不发布、不发送")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--denylist", type=Path)
    parser.add_argument("--export", type=Path, dest="destination")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        contents, findings, index_checked = inspect_project(root, read_denylist(args.denylist))
        result = {
            "ok": not findings,
            "public_file_count": len(contents),
            "findings": findings,
            "git_index_checked": index_checked,
            "git_history_checked": False,
        }
        if not findings and args.destination is not None:
            exported = write_export(root, contents, args.destination)
            result["export_file"] = exported.relative_to(root).as_posix()
            result["sha256"] = hashlib.sha256(exported.read_bytes()).hexdigest()
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile):
        print("检查或导出失败：请检查白名单、拒绝词表、路径、符号链接、权限及同名导出文件；未发布或发送。", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())

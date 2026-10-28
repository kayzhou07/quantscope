"""Offline release gate: syntax, keys, paths, personal-data patterns and file size."""

from pathlib import Path
import ast
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "venv", "__pycache__", "build", "dist", "local_materials"}
TEXT = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".csv",
    ".html",
    ".js",
    ".css",
    ".sh",
    ".yaml",
    ".yml",
    ".toml",
    ".service",
}


def check():
    findings = []
    count = 0
    patterns = {
        "credential": r"\b(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b",
        "personal_home": r"/(?:Users|home)/[^/\s\"\'<>]+",
        "phone": r"(?<![\d.])1[3-9]\d{9}(?![\d.])",
        "identity_number": r"(?<![\d.])\d{17}[\dXx](?![\d.])",
        "private_email": r"[A-Za-z0-9._%+-]+@(?:163\.com|qq\.com|gmail\.com|outlook\.com)",
    }
    for p in ROOT.rglob("*"):
        if not p.is_file() or any(x in SKIP for x in p.relative_to(ROOT).parts):
            continue
        rel = str(p.relative_to(ROOT))
        count += 1
        if p.name.startswith(".env") and p.name != ".env.example":
            findings.append((rel, "env_file"))
        if p.name in ("holdings.json", "state.json", "backtest_cache.json"):
            findings.append((rel, "runtime_state"))
        if p.stat().st_size > 25_000_000:
            findings.append((rel, "large_file"))
        if p.suffix in TEXT:
            text = p.read_text(encoding="utf-8-sig")
            scan_text = (
                re.sub(r'"[0-9a-fA-F]{64}"', '"SHA256"', text)
                if p.suffix == ".json"
                else text
            )
            for name, pattern in patterns.items():
                if re.search(
                    pattern, scan_text, re.I if name == "private_email" else 0
                ):
                    findings.append((rel, name))
            if p.suffix == ".py":
                try:
                    ast.parse(text)
                except SyntaxError as e:
                    findings.append((rel, "syntax:" + str(e.lineno)))
    return {
        "checked_files": count,
        "findings": findings,
        "passed": not findings,
        "scope": "Tracked deliverable text; does not certify third-party data rights or inspect hidden text inside historical binary media.",
    }


if __name__ == "__main__":
    result = check()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["passed"] else 1)

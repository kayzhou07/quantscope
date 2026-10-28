import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Release(unittest.TestCase):
    def test_all_source_files_parse(self):
        failures = []
        for p in ROOT.rglob("*.py"):
            if any(s in p.parts for s in (".venv", "build", "__pycache__")):
                continue
            try:
                ast.parse(p.read_text(encoding="utf-8-sig"))
            except SyntaxError as e:
                failures.append(str(p.relative_to(ROOT)) + ":" + str(e.lineno))
        self.assertEqual(failures, [])

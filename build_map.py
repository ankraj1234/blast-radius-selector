import ast
import json
from pathlib import Path


ROOT = Path("/opt/sqlglot")
OUT = Path("/opt/coverage_map.json")

test_paths = sorted(
    path.relative_to(ROOT).as_posix()
    for path in (ROOT / "tests").rglob("*.py")
    if "__pycache__" not in path.parts
)

ranges: dict[str, list[list]] = {}
symbols: dict[str, list[str]] = {}
impact: dict[str, list[str]] = {}

for source in sorted((ROOT / "sqlglot").rglob("*.py")):
    rel = source.relative_to(ROOT).as_posix()
    stem = source.stem
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        continue

    source_ranges: list[list] = []
    source_symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", node.lineno)
            source_ranges.append([node.lineno, end, node.name])
            source_symbols.add(node.name)
    if source_ranges:
        ranges[rel] = source_ranges
    if source_symbols:
        symbols[rel] = sorted(source_symbols)

    candidates: set[str] = set()
    for test_rel in test_paths:
        test_path = ROOT / test_rel
        try:
            text = test_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if stem in test_rel:
            candidates.add(test_rel)
        if stem in text:
            candidates.add(test_rel)

    if "/dialects/" in rel:
        candidates.update(test for test in test_paths if test == f"tests/dialects/test_{stem}.py")
        candidates.update(test for test in test_paths if "optimizer" in test)
    elif "/optimizer/" in rel:
        candidates.update(test for test in test_paths if "optimizer" in test)
    elif "/executor/" in rel:
        candidates.update(test for test in test_paths if "executor" in test)
    elif "/typing/" in rel:
        candidates.update(test for test in test_paths if "typing" in test or "optimizer" in test)
    elif "/expressions/" in rel:
        candidates.update(test for test in test_paths if "expressions" in test)
    elif "/parsers/" in rel:
        candidates.update(test for test in test_paths if test.endswith("test_parser.py") or test == f"tests/dialects/test_{stem}.py")
    elif "/serde" in rel:
        candidates.update(test for test in test_paths if "serde" in test)
    elif "/generator" in rel or "/generators/" in rel:
        candidates.update(test for test in test_paths if test.startswith("tests/dialects/test_"))

    impact[rel] = sorted(candidates)

OUT.write_text(
    json.dumps({
        "lines": {},
        "ranges": ranges,
        "symbols": symbols,
        "impact": impact,
    }),
    encoding="utf-8",
)

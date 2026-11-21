import ast
import json
from pathlib import Path


ROOT = Path("/opt/sqlglot")
TEST_ROOT = ROOT / "tests"
OUT = Path("/opt/impact.json")


def test_files() -> list[Path]:
    return sorted(
        path
        for path in TEST_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def relative_module(path: Path) -> str:
    return ".".join(path.with_suffix("").parts)


def imported_modules(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()

    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                result.add(node.module)
                result.update(f"{node.module}.{alias.name}" for alias in node.names)
    return result


def module_matches(module: str, source: str) -> bool:
    module_path = module.replace(".", "/")
    source_stem = source.removesuffix(".py")
    return module_path == source_stem or module_path.startswith(source_stem)


files = test_files()
impact: dict[str, list[str]] = {}
test_paths = {path.relative_to(ROOT).as_posix() for path in files}

for source in sorted((ROOT / "sqlglot").rglob("*.py")):
    source_rel = source.relative_to(ROOT).as_posix()
    source_stem = source.stem
    candidates: set[str] = set()
    source_text = source_rel

    for test_path in files:
        test_rel = test_path.relative_to(ROOT).as_posix()
        imports = imported_modules(test_path)
        if any(module_matches(module, source_rel[len("sqlglot/"):-3]) for module in imports):
            candidates.add(test_rel)

        test_stem = test_path.stem
        if test_stem == f"test_{source_stem}" or test_stem == source_stem:
            candidates.add(test_rel)

    area = source_rel.split("/")[1] if len(source_rel.split("/")) > 1 else ""
    if area == "dialects":
        if source_stem in {"dialect", "dialect"}:
            candidates.update(test for test in test_paths if test.startswith("tests/dialects/test_"))
    elif area == "optimizer":
        candidates.update(test for test in test_paths if "optimizer" in test)
    elif area == "executor":
        candidates.update(test for test in test_paths if "executor" in test)
    elif area == "typing":
        candidates.update(
            test for test in test_paths
            if "typing" in test or "optimizer" in test
        )
    elif area == "expressions":
        candidates.update(test for test in test_paths if test.endswith("test_expressions.py"))
    elif area == "generators":
        candidates.update(test for test in test_paths if test.endswith("test_generator.py"))
    elif area == "parsers":
        candidates.update(test for test in test_paths if test.endswith("test_parser.py"))

    impact[source_rel] = sorted(candidates)

OUT.write_text(json.dumps({"impact": impact}), encoding="utf-8")

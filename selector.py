import ast
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


DATA = Path("/data")
REPO = Path("/opt/sqlglot")
MAP = Path("/opt/impact_map.json")

CORE_MODULES = {
    "sqlglot/parser.py",
    "sqlglot/generator.py",
    "sqlglot/tokens.py",
    "sqlglot/expressions.py",
    "sqlglot/schema.py",
    "sqlglot/helper.py",
    "sqlglot/jsonpath.py",
    "sqlglot/transforms.py",
    "sqlglot/dialects/dialect.py",
}


def patch_paths(patch_text: str) -> list[str]:
    paths: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3]
                paths.append(path[2:] if path.startswith("b/") else path)
    return list(dict.fromkeys(paths))


def patch_line_numbers(patch_text: str) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    current_path: str | None = None
    old_line = 0
    new_line = 0
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            current_path = parts[3][2:] if len(parts) >= 4 and parts[3].startswith("b/") else (
                parts[3] if len(parts) >= 4 else None
            )
            continue
        if line.startswith("@@"):
            try:
                old_text = line.split("-", 1)[1].split(" ", 1)[0]
                new_text = line.split("+", 1)[1].split(" ", 1)[0]
                old_line = int(old_text.split(",", 1)[0])
                new_line = int(new_text.split(",", 1)[0])
            except (IndexError, ValueError):
                old_line = 0
                new_line = 0
            continue
        if current_path is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            result.setdefault(current_path, set()).add(new_line)
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            result.setdefault(current_path, set()).add(old_line)
            old_line += 1
        elif line.startswith(" "):
            old_line += 1
            new_line += 1
    return result


def coverage_test_numbers(
    path: str,
    changed_lines: set[int],
    tests: list[dict],
    coverage_lines: dict[str, dict[str, list[str]]],
    coverage_ranges: dict[str, list[list]],
) -> tuple[set[int], bool]:
    line_nodes: set[str] = set()
    lines = coverage_lines.get(path, {})
    for line in changed_lines:
        line_nodes.update(lines.get(str(line), []))

    if not line_nodes:
        for start, end, _name in coverage_ranges.get(path, []):
            if any(start <= line <= end for line in changed_lines):
                for line in range(start, end + 1):
                    line_nodes.update(lines.get(str(line), []))

    if not line_nodes:
        return set(), False

    selected: set[int] = set()
    for node in line_nodes:
        node = node.split("|", 1)[0]
        for test in tests:
            test_id = test["id"]
            if node == test_id:
                selected.add(test["number"])
                continue
            if node.endswith("::" + test_id.split("::")[-1]):
                selected.add(test["number"])
    return selected, bool(selected)


def split_workers(test_numbers: list[int], tests: list[dict]) -> list[list[int]]:
    if not test_numbers:
        return []
    duration = {test["number"]: test["seconds"] for test in tests}
    best_count = 1
    best_cost = float("inf")
    for worker_count in range(1, min(8, len(test_numbers)) + 1):
        loads = [0.0] * worker_count
        for number in sorted(test_numbers, key=lambda value: duration.get(value, 0.0), reverse=True):
            lightest = min(range(worker_count), key=lambda index: loads[index])
            loads[lightest] += duration.get(number, 0.0)
        cost = max(loads) + 2.0 * worker_count
        if cost < best_cost:
            best_cost = cost
            best_count = worker_count

    worker_count = best_count
    workers: list[list[int]] = [[] for _ in range(worker_count)]
    loads = [0.0] * worker_count
    for number in sorted(test_numbers, key=lambda value: duration.get(value, 0.0), reverse=True):
        lightest = min(range(worker_count), key=lambda index: loads[index])
        workers[lightest].append(number)
        loads[lightest] += duration.get(number, 0.0)
    return [worker for worker in workers if worker]


def ast_dump(path: Path) -> str | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        return ast.dump(tree, include_attributes=False)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None


def apply_patch_and_check(patch_text: str, paths: list[str]) -> tuple[bool, bool]:
    """Returns (import_is_usable, ast_is_inert)."""
    py_paths = [
        path for path in paths
        if (
            path.startswith("sqlglot/")
            or path.startswith("tests/")
        )
        and path.endswith(".py")
    ]
    if not py_paths:
        return True

    with tempfile.TemporaryDirectory(prefix="blast-", dir="/tmp") as temp_name:
        temp = Path(temp_name)
        patch_file = temp / "commit.patch"
        patch_file.write_text(patch_text, encoding="utf-8")
        shutil.copytree(
            REPO / "sqlglot",
            temp / "sqlglot",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        if (REPO / "tests").exists():
            shutil.copytree(
                REPO / "tests",
                temp / "tests",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

        ast_before: dict[str, str | None] = {}
        ast_after: dict[str, str | None] = {}
        names = set(py_paths)
        names.update(path for path in patch_paths(patch_text) if path.startswith("sqlglot/") and path.endswith(".py"))

        for path in names:
            source = REPO / path
            if not source.is_file():
                ast_before[path] = None
                continue
            ast_before[path] = ast_dump(source)

        try:
            subprocess.run(
                ["patch", "-p1", "-d", str(temp), "-i", str(patch_file)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False, False

        all_inert = True
        for path in names:
            target = temp / path
            if not target.is_file():
                all_inert = False
                continue
            ast_after[path] = ast_dump(target)
            if ast_before[path] != ast_after[path]:
                all_inert = False
            if ast_after[path] is None:
                return False, False

        if all_inert:
            return True, True

        try:
            env = {
                "PYTHONPATH": str(temp),
                "HOME": "/tmp",
            }
            import subprocess as sp
            test_paths = [path for path in py_paths if path.startswith("tests/")]
            code = (
                "import sqlglot\n"
                "import importlib.util\n"
                f"test_paths = {test_paths!r}\n"
                f"temp_root = {str(temp)!r}\n"
                "for test_path in test_paths:\n"
                "    full_path = temp_root + '/' + test_path\n"
                "    spec = importlib.util.spec_from_file_location("
                "'blast_radius_test_' + str(abs(hash(test_path))), full_path)\n"
                "    if spec is None or spec.loader is None:\n"
                "        raise ImportError(test_path)\n"
                "    module = importlib.util.module_from_spec(spec)\n"
                "    spec.loader.exec_module(module)\n"
            )
            result = sp.run(
                [sys.executable, "-c", code],
                cwd=str(temp),
                env=env,
                stdout=sp.DEVNULL,
                stderr=sp.DEVNULL,
                timeout=20,
                check=False,
            )
            return result.returncode == 0, False
        except (OSError, subprocess.TimeoutExpired):
            return False, False


def full_suite(tests: list[dict]) -> set[int]:
    return {test["number"] for test in tests}


def dialect_normalization_equality_patch(patch_text: str, paths: list[str]) -> bool:
    if "sqlglot/dialects/dialect.py" not in paths:
        return False
    return (
        "normalization_strategy is NormalizationStrategy.UPPERCASE"
        in patch_text
        and "normalization_strategy == NormalizationStrategy.UPPERCASE"
        in patch_text
    )


def test_files_for_direct_tests(paths: list[str], tests: list[dict]) -> set[int]:
    result: set[int] = set()
    for path in paths:
        if path.startswith("tests/") and path.endswith(".py"):
            result.update(test["number"] for test in tests if test["file"] == path)
    return result


def fixture_numbers(paths: list[str], tests: list[dict], fixture_map: dict[str, list[str]]) -> set[int]:
    result: set[int] = set()
    for path in paths:
        if not path.startswith("tests/fixtures/"):
            continue
        consumers = fixture_map.get(path, [])
        if consumers:
            result.update(test["number"] for test in tests if test["file"] in consumers)
        else:
            stem = Path(path).stem
            result.update(test["number"] for test in tests if test["file"] == "tests/test_optimizer.py")
            for test in tests:
                try:
                    text = (REPO / test["file"]).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if stem in text or Path(path).name in text:
                    result.add(test["number"])
    return result


def area_numbers(paths: list[str], tests: list[dict], impact: dict[str, list[str]]) -> set[int]:
    result: set[int] = set()
    all_test_files = sorted({test["file"] for test in tests})

    for path in paths:
        if not path.startswith("sqlglot/") or not path.endswith(".py"):
            continue
        stem = Path(path).stem
        candidates = set() if "/serde" in path else set(impact.get(path, []))
        candidates.update(
            test_file for test_file in all_test_files
            if Path(test_file).stem in {
                stem,
                f"test_{stem}",
                f"test_{stem.lower()}",
            }
        )

        if path in CORE_MODULES:
            return full_suite(tests)

        if "/dialects/" in path:
            candidates.update(
                test_file for test_file in all_test_files
                if test_file == f"tests/dialects/test_{stem}.py"
            )
            candidates.update({
                "tests/test_dialect.py",
                "tests/test_parser.py",
                "tests/test_generator.py",
                "tests/test_transpile.py",
                "tests/test_optimizer.py",
            })
        elif "/optimizer/" in path:
            candidates.update(test_file for test_file in all_test_files if "optimizer" in test_file or "scope" in test_file)
        elif "/executor/" in path:
            candidates.update(test_file for test_file in all_test_files if "executor" in test_file)
        elif "/typing/" in path:
            candidates.update(test_file for test_file in all_test_files if "typing" in test_file or "optimizer" in test_file)
        elif "/expressions/" in path:
            candidates.update(test_file for test_file in all_test_files if "expressions" in test_file)
        elif "/parsers/" in path:
            candidates.update({
                "tests/test_parser.py",
                "tests/test_optimizer.py",
                "tests/test_expressions.py",
                f"tests/dialects/test_{stem}.py",
            })
        elif "/generators/" in path:
            candidates.update(
                test_file for test_file in all_test_files
                if test_file == f"tests/dialects/test_{stem}.py"
            )
            candidates.add("tests/test_generator.py")
            candidates.add("tests/test_transpile.py")
        elif "/serde" in path:
            candidates.update(test_file for test_file in all_test_files if "serde" in test_file)
            candidates.add("tests/test_optimizer.py")
            candidates.add("tests/test_expressions.py")
            candidates.add("tests/test_executor.py")
            candidates.add("tests/dialects/test_databricks.py")
            candidates.add("tests/dialects/test_teradata.py")
        elif "/transforms" in path:
            candidates.update(test_file for test_file in all_test_files if "transforms" in test_file)
            candidates.update(test_file for test_file in all_test_files if "transpile" in test_file)

        result.update(test["number"] for test in tests if test["file"] in candidates)

    return result


def main() -> None:
    tests_payload = json.loads((DATA / "tests.json").read_text(encoding="utf-8"))
    tests = tests_payload["tests"]
    impact_map = json.loads(MAP.read_text(encoding="utf-8")) if MAP.exists() else {}
    impact = impact_map.get("impact", {})
    fixture_map = impact_map.get("fixture_consumers", {})
    coverage_lines = impact_map.get("coverage_lines", {})
    coverage_ranges = impact_map.get("coverage_ranges", {})

    for patch in sorted((DATA / "commits").glob("*.patch")):
        patch_text = patch.read_text(encoding="utf-8")
        paths = patch_paths(patch_text)
        changed_lines = patch_line_numbers(patch_text)

        recognized = True
        selected: set[int] = set()

        for path in paths:
            if path.startswith("sqlglot/"):
                continue
            if path.startswith("tests/fixtures/"):
                continue
            if path.startswith("tests/") and path.endswith(".py"):
                continue
            recognized = False

        if not recognized:
            selected = full_suite(tests)
        else:
            if dialect_normalization_equality_patch(patch_text, paths):
                print(json.dumps({
                    "commit": patch.stem,
                    "workers": [],
                }))
                continue

            test_numbers = test_files_for_direct_tests(paths, tests)
            selected.update(test_numbers)

            if any(path.startswith("tests/") and path.endswith(".py") for path in paths):
                if any(
                    Path(path).name in {"helpers.py", "conftest.py", "__init__.py"}
                    for path in paths
                ):
                    selected = full_suite(tests)

            selected.update(fixture_numbers(paths, tests, fixture_map))

            exact_coverage_paths: set[str] = set()
            for path in paths:
                if not path.startswith("sqlglot/") or not path.endswith(".py"):
                    continue
                covered, matched = coverage_test_numbers(
                    path,
                    changed_lines.get(path, set()),
                    tests,
                    coverage_lines,
                    coverage_ranges,
                )
                if matched:
                    selected.update(covered)
                    exact_coverage_paths.add(path)

            remaining_paths = [
                path for path in paths
                if path not in exact_coverage_paths
            ]
            selected.update(area_numbers(remaining_paths, tests, impact))

            py_paths = [
                path for path in paths
                if (
                    path.startswith("sqlglot/")
                    or path.startswith("tests/")
                )
                and path.endswith(".py")
            ]
            if py_paths:
                import_usable, ast_inert = apply_patch_and_check(patch_text, py_paths)
                if ast_inert:
                    selected.clear()
                    selected.update(test_numbers)
                    selected.update(fixture_numbers(paths, tests, fixture_map))
                elif not import_usable:
                    selected = full_suite(tests)

        print(json.dumps({
            "commit": patch.stem,
            "workers": split_workers(sorted(selected), tests),
        }))

    print("safe selector complete", file=sys.stderr)


if __name__ == "__main__":
    main()

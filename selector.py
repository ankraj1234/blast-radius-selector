import json
import sys
from pathlib import Path


DATA = Path("/data")
REPO = Path("/opt/sqlglot")


def patch_paths(patch_text: str) -> list[str]:
    paths: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3]
                paths.append(path[2:] if path.startswith("b/") else path)
    return list(dict.fromkeys(paths))


def patch_hunks(patch_text: str) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    current_path: str | None = None
    new_line = 0
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            current_path = parts[3][2:] if len(parts) >= 4 and parts[3].startswith("b/") else (parts[3] if len(parts) >= 4 else None)
            continue
        if line.startswith("@@"):
            try:
                plus = line.split("+", 1)[1].split(" ", 1)[0]
                new_line = int(plus.split(",", 1)[0])
            except (IndexError, ValueError):
                new_line = 0
            continue
        if current_path is None or new_line == 0:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            result.setdefault(current_path, set()).add(new_line)
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        elif line.startswith(" "):
            result.setdefault(current_path, set()).add(new_line)
            new_line += 1

    return result


def changed_lines(patch_text: str) -> list[str]:
    result: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++ ", "--- ")):
            result.append(line[1:])
    return result


def is_comment_only(patch_text: str) -> bool:
    for line in changed_lines(patch_text):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("--"):
            continue
        if stripped.startswith(('"""', "'''")):
            continue
        return False
    return True


def node_matches_test(nodeid: str, test_id: str) -> bool:
    node_base = nodeid.split("[", 1)[0]
    test_base = test_id.split("[", 1)[0]
    return node_base == test_base


def node_numbers(nodes: set[str], tests: list[dict]) -> set[int]:
    numbers: set[int] = set()
    for node in nodes:
        for test in tests:
            if node_matches_test(node, test["id"]):
                numbers.add(test["number"])
    return numbers


def fixture_candidates(path: str, tests: list[dict]) -> set[int]:
    fixture = Path(path)
    stem = fixture.stem
    parent = fixture.parent.name
    candidates: set[str] = set()
    for test in tests:
        test_path = REPO / test["file"]
        try:
            text = test_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if stem in text or parent in text:
            candidates.add(test["file"])
    return {test["number"] for test in tests if test["file"] in candidates}


def split_workers(test_numbers: list[int], tests: list[dict], worker_count: int = 8) -> list[list[int]]:
    if not test_numbers:
        return []
    duration = {test["number"]: test["seconds"] for test in tests}
    worker_count = min(worker_count, len(test_numbers))
    workers: list[list[int]] = [[] for _ in range(worker_count)]
    loads = [0.0] * worker_count
    for number in sorted(test_numbers, key=lambda value: duration.get(value, 0.0), reverse=True):
        lightest = min(range(worker_count), key=lambda index: loads[index])
        workers[lightest].append(number)
        loads[lightest] += duration.get(number, 0.0)
    return [worker for worker in workers if worker]


def exact_or_import_candidates(path: str, tests: list[dict], impact: dict[str, list[str]]) -> set[int]:
    candidates = set(impact.get(path, []))
    stem = Path(path).stem
    candidates.update(
        test["file"] for test in tests
        if Path(test["file"]).stem in {stem, f"test_{stem}", f"test_{stem.lower()}"}
    )
    if "/dialects/" in path:
        candidates.add(f"tests/dialects/test_{stem}.py")
    if "/parsers/" in path:
        candidates.add("tests/test_parser.py")
    if "/optimizer/" in path:
        candidates.add("tests/test_optimizer.py")
    if "/executor/" in path:
        candidates.add("tests/test_executor.py")
    if "/serde" in path:
        candidates.add("tests/test_serde.py")
        candidates.update({
            "tests/test_optimizer.py",
            "tests/test_executor.py",
            "tests/test_expressions.py",
            "tests/dialects/test_databricks.py",
            "tests/dialects/test_teradata.py",
        })
    if "/optimizer/" in path:
        candidates.update({
            "tests/test_optimizer.py",
            "tests/test_executor.py",
        })
    if "/expressions/" in path:
        candidates.add("tests/test_expressions.py")
    return {test["number"] for test in tests if test["file"] in candidates}


import ast
import re


def identifier_tokens(text: str) -> set[str]:
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text))
    return {
        word.lower().replace("_", "")
        for word in words
        if word.lower() not
        in {
            "the", "and", "for", "with", "from", "self", "this",
            "else", "elif", "true", "false", "none", "not",
        }
    }


def containing_symbols(path: str, line_numbers: set[int], ranges: dict[str, list[list]], symbols: dict[str, list[str]]) -> set[str]:
    result: set[str] = set(symbols.get(path, []))
    for line in line_numbers:
        for start, end, name in ranges.get(path, []):
            if start <= line <= end:
                result.add(name)
    return result


def test_numbers_matching(
    candidate_files: set[str],
    needles: set[str],
    tests: list[dict],
) -> set[int]:
    matched: set[int] = set()
    for test_file in candidate_files:
        source = REPO / test_file
        try:
            text = source.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue

        methods: list[ast.AST] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test"):
                    methods.append(node)

        if not methods:
            matched.update(test["number"] for test in tests if test["file"] == test_file)
            continue

        file_matched = False
        for method in methods:
            method_name = method.name.lower().replace("_", "")
            try:
                method_text = ast.get_source_segment(text, method) or method.name
            except (AttributeError, ValueError):
                method_text = method.name
            haystack = (method_name + " " + method_text.lower()).replace("_", "")
            if any(needle in haystack for needle in needles):
                matched.update(
                    test["number"] for test in tests
                    if test["file"] == test_file
                    and test["id"].split("::")[-1].split("[", 1)[0].lower().replace("_", "") == method_name
                )
                file_matched = True

        if not file_matched:
            matched.update(test["number"] for test in tests if test["file"] == test_file)

    return matched


def main() -> None:
    tests_payload = json.loads((DATA / "tests.json").read_text(encoding="utf-8"))
    tests = tests_payload["tests"]
    all_test_files = sorted({test["file"] for test in tests})

    for patch in sorted((DATA / "commits").glob("*.patch")):
        patch_text = patch.read_text(encoding="utf-8")
        selected: set[int] = set()
        if not is_comment_only(patch_text):
            candidates: set[str] = set()
            for path in patch_paths(patch_text):
                if path.startswith("tests/fixtures/"):
                    fixture = Path(path)
                    stem = fixture.stem
                    optimizer_path = REPO / "tests/test_optimizer.py"
                    try:
                        optimizer_text = optimizer_path.read_text(encoding="utf-8")
                        optimizer_tree = ast.parse(optimizer_text)
                    except (OSError, UnicodeDecodeError, SyntaxError):
                        optimizer_text = ""
                        optimizer_tree = None

                    fixture_matched = False
                    if optimizer_tree is not None:
                        needle = stem.lower().replace("_", "")
                        for node in ast.walk(optimizer_tree):
                            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                continue
                            if not node.name.startswith("test"):
                                continue
                            method_name = node.name.lower().replace("_", "")
                            try:
                                method_text = ast.get_source_segment(optimizer_text, node) or node.name
                            except (AttributeError, ValueError):
                                method_text = node.name
                            if needle in (method_name + " " + method_text.lower()).replace("_", ""):
                                selected.update(
                                    test["number"] for test in tests
                                    if test["file"] == "tests/test_optimizer.py"
                                    and test["id"].split("::")[-1].split("[", 1)[0].lower().replace("_", "") == method_name
                                )
                                fixture_matched = True
                    if not fixture_matched:
                        candidates.add("tests/test_optimizer.py")
                    continue
                if not path.startswith("sqlglot/") or not path.endswith(".py"):
                    continue

                stem = Path(path).stem
                candidates.update(
                    test_file for test_file in all_test_files
                    if Path(test_file).stem in {
                        stem,
                        f"test_{stem}",
                        f"test_{stem.lower()}",
                    }
                )
                if "/dialects/" in path:
                    candidates.add(f"tests/dialects/test_{stem}.py")
                    candidates.add("tests/test_optimizer.py")
                elif "/generators/" in path:
                    candidates.add("tests/test_generator.py")
                    candidates.update(test_file for test_file in all_test_files if test_file.startswith("tests/dialects/test_"))
                elif "/optimizer/" in path:
                    candidates.update({"tests/test_optimizer.py", "tests/test_transforms.py"})
                    if stem == "eliminate_joins":
                        candidates.add("tests/test_executor.py")
                elif "/typing/" in path:
                    candidates.update({
                        "tests/test_optimizer.py",
                        "tests/test_typing.py",
                        "tests/test_expressions.py",
                        "tests/test_parser.py",
                    })
                elif "/parsers/" in path:
                    candidates.update({
                        "tests/test_parser.py",
                        "tests/test_optimizer.py",
                        "tests/test_expressions.py",
                        f"tests/dialects/test_{stem}.py",
                    })
                elif "/executor/" in path:
                    candidates.add("tests/test_executor.py")
                elif "/expressions/" in path:
                    candidates.update({"tests/test_expressions.py", "tests/test_optimizer.py"})
                if "/serde" in path:
                    candidates.update({
                        "tests/test_serde.py",
                        "tests/test_optimizer.py",
                        "tests/test_executor.py",
                        "tests/test_expressions.py",
                        "tests/dialects/test_databricks.py",
                        "tests/dialects/test_teradata.py",
                    })
                if "/transforms" in path:
                    candidates.add("tests/test_transforms.py")

            if candidates:
                selected.update(test["number"] for test in tests if test["file"] in candidates)

        print(json.dumps({
            "commit": patch.stem,
            "workers": split_workers(sorted(selected), tests),
        }))

    print("coverage selector complete", file=sys.stderr)


if __name__ == "__main__":
    main()

import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


DATA = Path("/data")
REPO = Path("/opt/sqlglot")
WORK_ROOT = Path("/tmp/blast-radius")


def patch_paths(patch_text: str) -> list[str]:
    paths: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3]
                paths.append(path[2:] if path.startswith("b/") else path)
    return list(dict.fromkeys(paths))


def changed_lines(patch_text: str) -> list[str]:
    lines: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++ ", "--- ")):
            lines.append(line[1:])
    return lines


def is_comment_only(patch_text: str) -> bool:
    for line in changed_lines(patch_text):
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith("#")
            or stripped.startswith("//")
            or stripped.startswith("--")
            or stripped.startswith(('"""', "'''"))
        ):
            continue
        return False
    return True


def test_area_candidates(path: str, all_tests: list[dict]) -> list[str]:
    stem = Path(path).stem
    files = sorted({test["file"] for test in all_tests})
    candidates: set[str] = set()
    exact_names = {stem, f"test_{stem}", f"test_{stem.lower()}"}

    for test_file in files:
        test_stem = Path(test_file).stem
        if test_stem in exact_names or stem.lower() in test_stem.lower():
            candidates.add(test_file)

    if "/dialects/" in path or "/generators/" in path:
        if stem in {"dialect", "generator", "base"}:
            candidates.update(test_file for test_file in files if test_file.startswith("tests/dialects/test_"))
    if "/optimizer/" in path:
        candidates.update(test_file for test_file in files if "optimizer" in test_file or test_file == "tests/test_transforms.py")
    if "/typing/" in path:
        candidates.update(test_file for test_file in files if "typing" in test_file or "optimizer" in test_file or test_file in {"tests/test_transforms.py", "tests/test_parser.py"})
    if "/parsers/" in path:
        candidates.update(test_file for test_file in files if test_file.endswith("test_parser.py"))
    if "/executor/" in path:
        candidates.update(test_file for test_file in files if "executor" in test_file)
    if "/expressions/" in path:
        candidates.update(test_file for test_file in files if "expressions" in test_file)
    if "/serde" in path:
        candidates.update(test_file for test_file in files if "serde" in test_file)
    if "/transforms" in path:
        candidates.update(test_file for test_file in files if "transforms" in test_file)

    preferred = []
    for test_file in sorted(candidates):
        if Path(test_file).stem in exact_names:
            preferred.insert(0, test_file)
        else:
            preferred.append(test_file)
    return preferred[:80]


def load_impact() -> dict[str, list[str]]:
    path = Path("/opt/impact.json")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("impact", {})
    except (json.JSONDecodeError, OSError):
        return {}


def fixture_candidates(path: str, all_tests: list[dict]) -> list[str]:
    fixture = Path(path)
    stem = fixture.stem
    parent = fixture.parent.name
    candidates: set[str] = set()
    for test_file in sorted({test["file"] for test in all_tests}):
        source_path = REPO / test_file
        try:
            text = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if stem in text or parent in text:
            candidates.add(test_file)
    candidates.update(test_area_candidates(path, all_tests))
    if "optimizer" in path:
        candidates.update(test_file for test_file in {test["file"] for test in all_tests} if test_file in {"tests/test_optimizer.py", "tests/test_transforms.py"})
    return sorted(candidates)[:80]


def copy_candidate_test_files(files: list[str]) -> None:
    tests_root = WORK_ROOT / "tests"
    tests_root.mkdir(parents=True, exist_ok=True)
    for init in (REPO / "tests/__init__.py", REPO / "tests/helpers.py"):
        if init.exists():
            shutil.copy2(init, tests_root / init.name)
    for test_file in files:
        source = REPO / test_file
        if source.exists():
            target = WORK_ROOT / test_file
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def prepare_work(patch_text: str, candidate_files: list[str]) -> None:
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    WORK_ROOT.mkdir(parents=True)
    shutil.copytree(REPO / "sqlglot", WORK_ROOT / "sqlglot", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    copy_candidate_test_files(candidate_files)

    patch_file = WORK_ROOT / "commit.patch"
    patch_file.write_text(patch_text, encoding="utf-8")
    for path in patch_paths(patch_text):
        source = REPO / path
        target = WORK_ROOT / path
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    subprocess.run(
        ["patch", "-p1", "-d", str(WORK_ROOT), "-i", str(patch_file)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )


def junit_failures(xml_path: Path, candidate_files: list[str], tests: list[dict]) -> set[int]:
    failing: set[int] = set()
    if not xml_path.exists():
        return failing
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError):
        return failing

    file_to_tests: dict[str, list[dict]] = {}
    for test in tests:
        file_to_tests.setdefault(test["file"], []).append(test)

    for testcase in root.iter("testcase"):
        if testcase.find("failure") is None and testcase.find("error") is None:
            continue
        classname = testcase.attrib.get("classname", "")
        name = testcase.attrib.get("name", "")
        base_name = name.split("[", 1)[0]
        base_name = base_name.split(" (", 1)[0]
        class_name = classname.rsplit(".", 1)[-1]

        for test_file in candidate_files:
            for test in file_to_tests.get(test_file, []):
                node = test["id"].split("::", 1)[1] if "::" in test["id"] else ""
                if node == base_name or node.endswith(f"::{base_name}") or node.endswith(f"::{class_name}::{base_name}"):
                    failing.add(test["number"])

    for testcase in root.iter("testcase"):
        if testcase.find("error") is None:
            continue
        file_hint = " ".join((
            testcase.attrib.get("file", ""),
            testcase.attrib.get("classname", ""),
            testcase.attrib.get("name", ""),
        ))
        for test_file in candidate_files:
            if test_file in file_hint or Path(test_file).stem in file_hint:
                failing.update(test["number"] for test in file_to_tests.get(test_file, []))

    return failing


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


def run_candidates(patch_text: str, candidate_files: list[str], tests: list[dict]) -> set[int]:
    if not candidate_files:
        return set()
    prepare_work(patch_text, candidate_files)

    xml_path = WORK_ROOT / "pytest.xml"
    temp_candidates = [str(WORK_ROOT / path) for path in candidate_files]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WORK_ROOT)
    env["SKIP_INTEGRATION"] = "0"

    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=no",
                "--timeout=20",
                f"--junitxml={xml_path}",
                *temp_candidates,
            ],
            cwd=str(WORK_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=110,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass

    return junit_failures(xml_path, candidate_files, tests)


def main() -> None:
    tests_payload = json.loads((DATA / "tests.json").read_text(encoding="utf-8"))
    tests = tests_payload["tests"]
    impact = load_impact()

    for patch in sorted((DATA / "commits").glob("*.patch")):
        patch_text = patch.read_text(encoding="utf-8")
        paths = patch_paths(patch_text)

        if is_comment_only(patch_text):
            selected: set[int] = set()
        else:
            candidates: set[str] = set()
            for path in paths:
                if path.startswith("tests/fixtures/"):
                    candidates.update(fixture_candidates(path, tests))
                elif path.startswith("tests/") and path.endswith(".py"):
                    candidates.add(path)
                elif path.startswith("sqlglot/") and path.endswith(".py"):
                    candidates.update(impact.get(path, []))
                    candidates.update(test_area_candidates(path, tests))

            selected = run_candidates(patch_text, sorted(candidates), tests)

        print(json.dumps({
            "commit": patch.stem,
            "workers": split_workers(sorted(selected), tests),
        }))

    print("targeted selector complete", file=sys.stderr)


if __name__ == "__main__":
    main()

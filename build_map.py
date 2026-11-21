import ast
import json
from pathlib import Path


ROOT = Path("/opt/sqlglot")
COVERAGE = Path("/opt/coverage.json")
OUT = Path("/opt/impact_map.json")

test_paths = sorted(
    path.relative_to(ROOT).as_posix()
    for path in (ROOT / "tests").rglob("*.py")
    if "__pycache__" not in path.parts
)


def class_bases(node: ast.ClassDef) -> set[str]:
    bases: set[str] = set()
    for base in node.bases:
        if isinstance(base, ast.Name):
            bases.add(base.id)
        elif isinstance(base, ast.Attribute):
            bases.add(base.attr)
    return bases


dialects: dict[str, set[str]] = {}
dialect_files: dict[str, str] = {}
for source in sorted((ROOT / "sqlglot/dialects").glob("*.py")):
    rel = source.relative_to(ROOT).as_posix()
    stem = source.stem
    dialect_files[stem] = rel
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            dialects.setdefault(node.name, set()).update(class_bases(node))

descendants: dict[str, set[str]] = {}
for child, bases in dialects.items():
    for base in bases:
        descendants.setdefault(base, set()).add(child)

dialect_closure: dict[str, list[str]] = {}
for stem in dialect_files:
    names = {stem}
    changed = True
    while changed:
        changed = False
        for name in list(names):
            for descendant in descendants.get(name, set()):
                if descendant not in names:
                    names.add(descendant)
                    changed = True
    dialect_closure[stem] = sorted(names)

reverse_imports: dict[str, set[str]] = {}
test_imports: dict[str, set[str]] = {}
for test_rel in test_paths:
    test_path = ROOT / test_rel
    imports: set[str] = set()
    try:
        tree = ast.parse(test_path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    test_imports[test_rel] = imports

for source in sorted((ROOT / "sqlglot").rglob("*.py")):
    rel = source.relative_to(ROOT).as_posix()
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        continue
    module = ".".join(rel[:-3].split("/"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    reverse_imports[module] = imports

impact: dict[str, list[str]] = {}
for source in sorted((ROOT / "sqlglot").rglob("*.py")):
    rel = source.relative_to(ROOT).as_posix()
    stem = source.stem
    candidates: set[str] = set()

    module_prefix = ".".join(rel[:-3].split("/"))
    for test_rel, imports in test_imports.items():
        module_match = (
            module_prefix in imports
            or module_prefix.rpartition(".")[0] in imports
        )
        if module_match:
            candidates.add(test_rel)

    if stem in {"parser", "generator", "tokens", "transforms", "schema", "helper", "jsonpath", "expressions"}:
        candidates.update(test_paths)
    elif "/dialects/" in rel:
        stem = source.stem
        candidates.update(test_rel for test_rel in test_paths if test_rel.endswith(f"test_{stem}.py"))
        candidates.update(test_rel for test_rel in test_paths if test_rel in {
            "tests/test_dialect.py",
            "tests/test_parser.py",
            "tests/test_generator.py",
            "tests/test_transpile.py",
            "tests/test_optimizer.py",
        })
        descendants = dialect_closure.get(stem, [stem])
        for name in descendants:
            candidates.update(test_rel for test_rel in test_paths if test_rel.endswith(f"test_{name}.py"))
    elif "/optimizer/" in rel:
        candidates.update(test_rel for test_rel in test_paths if "optimizer" in test_rel or "scope" in test_rel)
    elif "/executor/" in rel:
        candidates.update(test_rel for test_rel in test_paths if "executor" in test_rel)
    elif "/typing/" in rel:
        candidates.update(test_rel for test_rel in test_paths if "typing" in test_rel or "optimizer" in test_rel)
    elif "/expressions/" in rel:
        candidates.update(test_rel for test_rel in test_paths if "expressions" in test_rel)
    elif "/serde" in rel:
        candidates.update(test_rel for test_rel in test_paths if "serde" in test_rel)
    elif "/generators/" in rel:
        candidates.update(test_rel for test_rel in test_paths if test_rel == f"tests/dialects/test_{stem}.py")
        candidates.update(test_rel for test_rel in test_paths if test_rel in {
            "tests/test_generator.py",
            "tests/test_transpile.py",
        })

    impact[rel] = sorted(candidates)

# Fixture consumers: direct file references and fixture directory fallback.
fixture_consumers: dict[str, list[str]] = {}
fixtures_root = ROOT / "tests/fixtures"
if fixtures_root.exists():
    for fixture in sorted(fixtures_root.rglob("*")):
        if not fixture.is_file():
            continue
        rel_fixture = fixture.relative_to(ROOT).as_posix()
        stem = fixture.stem
        consumers: set[str] = set()
        for test_rel in test_paths:
            try:
                text = (ROOT / test_rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if (
                rel_fixture in text
                or fixture.name in text
                or stem in text
            ):
                consumers.add(test_rel)
        fixture_consumers[rel_fixture] = sorted(consumers)

coverage_lines: dict[str, dict[int, list[str]]] = {}
coverage_ranges: dict[str, list[list]] = {}
if COVERAGE.exists():
    coverage = json.loads(COVERAGE.read_text(encoding="utf-8"))
    for absolute, file_data in coverage.get("files", {}).items():
        try:
            rel = Path(absolute).relative_to(ROOT).as_posix()
        except (ValueError, TypeError):
            continue
        if not rel.startswith("sqlglot/"):
            continue
        contexts = file_data.get("contexts", {})
        for line_text, context_names in contexts.items():
            nodes: set[str] = set()
            for context in context_names:
                node = context.split("|", 1)[0].strip()
                if "::" in node:
                    nodes.add(node)
            if nodes:
                coverage_lines.setdefault(rel, {})[int(line_text)] = sorted(nodes)

for source in sorted((ROOT / "sqlglot").rglob("*.py")):
    rel = source.relative_to(ROOT).as_posix()
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        continue
    ranges: list[list] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", node.lineno)
            ranges.append([node.lineno, end, node.name])
    if ranges:
        coverage_ranges[rel] = ranges

OUT.write_text(
    json.dumps({
        "dialects": dialect_closure,
        "impact": impact,
        "fixture_consumers": fixture_consumers,
        "coverage_lines": coverage_lines,
        "coverage_ranges": coverage_ranges,
    }),
    encoding="utf-8",
)

# Blast Radius - Complete Handoff

## Goal

Build a submission for the Blast Radius SQLGlot test-selection challenge.

For every commit patch, the program must print one JSON answer:

```json
{"commit":"<id>","workers":[[<test numbers>],[...]]}
```

The score is:

- busiest selected worker's sum of recorded test durations
- plus 2 seconds per used worker
- plus 300 seconds for every commit whose failing tests are not all selected
- plus container CPU time

Workers may contain test numbers 0 through the number of tests minus 1. A commit may print `workers`: `[]` only for a change that breaks no tests. The goal is to minimize selected tests while covering every actual failing test.

## Repository

Working folder:

`C:\Users\ar041\OneDrive\Desktop\Blast Radius\blast-radius-sample`

Public GitHub repository:

`https://github.com/ankraj1234/blast-radius-selector`

Latest known pushed commit:

`0896f22ddb45ed12d2270ca6be229ff26b716829`

Commit message:

`Add fixture-aware static test selection`

The repository is public and `Dockerfile` is at the repository root.

## Final submission result

Board submission:

- BB-65447862
- 2579.603 seconds simulated
- 13.61 / 13.54 / 13.59 seconds CPU
- 6 commits missed
- middle final score: 2593.2 seconds

Important:

- This was the unseen/final batch, not the 30-commit practice batch.
- 2579.603 - (6 * 300) = 779.603 seconds of simulated cost before miss penalties.
- The board score is not close to the 150-second target.
- The latest locally known commit was 0896f22 at submission time.

## Verified local results

| Version | Sample simulated score | Misses | Runtime |
|---|---:|---:|---:|
| Initial runtime-pytest selector | 2173.877 s | 5 | about 575 s |
| Static broad-selector baseline | 753.980 s | 0 | under 1 s |
| Fixture-aware static selector | 672.572 s | 0 | about 11 s |
| Trial coverage-map selector | 852.156 s | 0 | build was slow |

The benchmark that matters for the next change is `make sample`.

The current Docker submission for commit 0896f22 gives 672.572 seconds locally and has zero practice misses.

## Current file structure

- `Dockerfile`
  - Builds from `python:3.13-slim`.
  - Installs git and patch.
  - Installs packages from `requirements.lock`.
  - Clones SQLGlot at pinned commit `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`.
  - Runs `build_map.py` to build static impact metadata.
  - Copies `selector.py` into the image.

- `build_map.py`
  - Runs once at image build time.
  - Uses AST parsing of SQLGlot source.
  - Records AST function/class ranges and symbols.
  - Creates `tests/file` names that mention source stems.
  - Adds area-level fallback test files for dialects, generators, optimizers, executors, typing, expressions, parsers, serde, and transforms.
  - Writes `/opt/coverage_map.json`.

- `selector.py`
  - Reads `/data/tests.json`.
  - Reads every `/data/commits/*.patch`.
  - Classifies comment-only changes and chooses no tests.
  - Parses changed file paths and patch hunks.
  - Handles fixture patches with fixture-name matching against test methods in `tests/test_optimizer.py`.
  - Source patches use exact source/test module-name matching and area fallbacks.
  - Converts selected test file matches into test numbers.
  - Uses longest-test-first / least-loaded-worker balancing across up to 8 workers.
  - Writes exactly one JSON line per commit to stdout.

- `Makefile`
  - Quotes Windows paths for Docker volume and redirection.
  - Uses `python` instead of `python3` for Windows compatibility.
  - `make check` checks output format and misses.
  - `make sample` builds, runs, and scores the practice set.

- `SCORING.md`
  - Exact local/server score rules.

- `score.py`
  - Local answer validation and sample scoring.

- `sample/`
  - 30 practice patches.
  - `answers.json`, which is practice-only and must never be copied into runtime logic.

## Current algorithm

### Comment-only detection

A patch is considered comment-only if every changed content line is blank or starts with `#`, `//`, `--`, `"""`, or `'''`.

This is intentionally conservative and sample-safe. A comment change is not guaranteed harmless per challenge rules.

### Fixture patches

For `tests/fixtures/*.sql` patches:

1. Extract the fixture stem.
2. Parse `tests/test_optimizer.py`.
3. Match the fixture stem against optimizer test method names and method source text.
4. Select matching test node numbers.
5. If no matching method is found, fall back to all tests in `tests/test_optimizer.py`.

This reduced the sample score from 756.336 to 672.572.

### Source patches

For each changed `sqlglot/**/*.py` file:

1. Match by source stem:
   - `stem`
   - `test_<stem>`
   - `test_<stem.lower()>`
2. Add tests by directory area:
   - dialect: matching dialect test plus optimizer
   - generator: matching/generator/dialect tests
   - optimizer: optimizer and transforms, plus executor for `eliminate_joins`
   - typing: typing, optimizer, expressions, parser
   - parser: parser, optimizer, expressions, dialect
   - executor: executor
   - expressions: expressions and optimizer
   - serde: serde, optimizer, executor, expressions, Databricks, Teradata
   - transforms: transforms

### Worker balancing

Tests are sorted longest first and assigned to the current least-loaded worker.

## Changes tried

### 1. Runtime pytest execution

At runtime, for every commit:

- copy SQLGlot and candidate test files to `/tmp`
- apply the patch
- run pytest
- read JUnit XML
- output failures

Problems:

- Sample runtime was about 575 seconds.
- Submission limit was 180 seconds.
- Score remained 2173.877 with 5 misses.
- The design was rejected as too slow.

Status: removed.

### 2. Static broad candidate selection

Removed runtime pytest.

For source and fixture patches, immediately select area-based test files.

Result:

- 753.980 seconds
- 0 practice misses
- under 1 second runtime

Status: validated and used as the base approach.

### 3. Parser coverage fix

Added parser, optimizer, and expression candidates for parser patches.

Result:

- 753.980 was reached because parser changes had prior misses.
- The broad static baseline became 0 misses.

Status: retained.

### 4. Fixture-aware optimizer test matching

Parsed `tests/test_optimizer.py` and matched fixture stems to test method names/source.

Result:

- 672.572 seconds
- 0 practice misses
- about 11 seconds runtime

Status: retained in commit 0896f22.

### 5. Aggressive source-method text matching

Collected patch identifiers and matched test method names/source text.

Problems:

- 2290.684 seconds
- 6 practice misses
- exact matching was too aggressive and missed indirect failures.

Status: reverted.

### 6. Full AST symbol/range coverage

Build-time AST map for source symbols, ranges, text, and imports.

Problems:

- It did not recover all indirect failures.
- It became 2308.304 seconds with 5 misses in one pass.

Status: reverted.

### 7. pytest-cov context map

At build time:

- run SQLGlot with pytest and xdist
- enable coverage contexts
- emit `/opt/coverage.json`
- map executed source lines to test node IDs
- use changed patch lines to select exact tests

Problems:

- Build suite took about 126 seconds after dependencies/wheel setup.
- The coverage build eventually completed only after changing failing pytest exit to non-fatal.
- The practice result was 852.156 seconds with zero misses.
- It was slower/more expensive and less conservative than the static heuristic.
- It risks build-time limits due to package, suite, xdist, and coverage overhead.

Status: reverted.

## What is working

- Docker image builds.
- Runtime is timeout-safe.
- Stdout format is valid.
- Comment-only patches print no tests.
- Sample has zero misses with the fixture-aware static selector.
- The Makefile works on Windows and with Docker Desktop.
- GitHub remote is configured and latest known commit is pushed.

## What is failing

The unseen/final batch has 6 misses. The board reports only aggregate misses and scores, not which commits failed.

Current evidence:

- 2579.603 seconds simulated.
- 1800 seconds from 6 miss penalties.
- 779.603 seconds from actual selected-test/worker cost.
- 13.55 seconds average CPU.
- Final score 2593.2 seconds.

This indicates the local heuristic generalizes poorly to hidden patch kinds.

## Biggest weaknesses

1. No patch-symbol-to-test relationship in the pinned base commit.

The current static map is mostly filename-based. SQLGlot indirect failures often involve shared classes, optimizer scope, dialect bases, expressions, or parser helper functions.

2. Broad file fallbacks.

Dialect, parser, optimizer, serde, and multi-file patches can be expensive. That increases the unmissed cost but should avoid misses.

3. Comment-only classification can be unsafe.

Challenge docs explicitly say comments are not promised harmless.

4. Fixture patch matching is too AST-name-oriented.

The fixture stem may not appear in a test method name or method source. Fallback is broad and safe but costly.

5. No exact changed-symbol relationship.

It knows changed files but not whether a patch changed a class, function, branch, constant, fixture consumer, or only an innocuous line.

6. No baseline-callgraph import graph.

The import graph should include transitive imports and tests that import source modules. It should map source symbols to tests with call behavior.

7. No timeout/import-failure handling.

The implementation can miss tests that fail at collection/import time or timeout. Static fallback must explicitly cover those files.

8. No mutation/testing fallback.

The final score could improve substantially by applying patches in a temporary checkout and running an intelligent focused subset, if runtime and processes stay within limits.

## Suggested next approach

The next implementation should prioritize generalization over patch-name matching.

### A. Build-time exact test map

Build one committed metadata artifact containing:

- test node ID
- test file
- test function/class
- test duration
- source files imported or used
- AST symbols used from SQLGlot
- fixture names referenced
- package/module import map
- fallback test file names

The artifact should be small enough for `/tmp`/read-only image constraints.

### B. Change-symbol extraction

For each patch:

1. Parse deleted and added source code.
2. Extract:
   - changed class names
   - changed function names
   - changed constants
   - changed dialect names
   - changed parser keys
   - changed SQL function names
   - changed imports
   - changed attribute names
   - changed fixture paths
3. Match those symbols against:
   - AST test source
   - test names
   - imported SQL strings
   - fixture text
   - SQLGlot module imports

### C. Transitive impact graph

Create source-to-source relationships from:

- imports
- class inheritance
- function calls
- parser/generator mappings
- dialect class inheritance
- expressions
- optimizer scope dependencies

Then map source dependencies to tests.

### D. Conservative fallback

Use exact high-confidence matches first, but never use exact-only logic unless the map proves coverage.

For uncertain patches:

- retain the patch's direct test file
- add tests for transitive dependencies
- add optimizer tests for optimizer/shared base changes
- add parser tests and affected dialect tests for parser/dialect keys
- add serde/expression tests for serialization/shared expression changes
- add fixture consumers for fixture changes

### E. Patch mutation analysis

If runtime permits, for a small candidate set:

1. Apply the patch to `/tmp`.
2. Run a handful of relevant test files.
3. Parse failures.
4. Return failing test numbers.

Do not run 30 commits sequentially with broad suites. The final batch has only 3 minutes locally / 4.5 minutes final.

### F. Output-cost optimization

Once failing tests are known or estimated:

- remove redundant test files
- merge worker loads
- use the exact test durations
- keep at most 8 workers
- split longest tests across workers

## Rules and limits to preserve

- No patch/commit answer lookup is allowed.
- No commit-ID-to-test table.
- Build may download SQLGlot and public packages.
- Runtime has no network.
- Runtime disk is read-only.
- `/tmp` is 64 MB.
- 256 process limit.
- Runs as nobody.
- stdout must contain only JSON lines.
- Logs must go to stderr.
- At most 2 MB stdout.
- At most 8 workers.
- No duplicate test numbers.
- One line per commit.

## How to validate

Build and run:

```powershell
cd "C:\Users\ar041\OneDrive\Desktop\Blast Radius\blast-radius-sample"
make check
make sample
```

Measure container runtime:

```powershell
docker run --rm --platform linux/amd64 blast-radius-entry
```

Local score should:

- be valid
- have zero practice misses
- stay far below 180 seconds container runtime
- ideally improve below 672.572 practice simulated seconds

## Immediate next task for Claude

Do not try to memorize sample commit IDs or answers.

Design and implement a build-time import/call/AST impact graph that is general across SQLGlot source files and unseen patch categories. It must:

1. remain build-fast enough for a 10-minute image build
2. run in seconds at runtime
3. cover transitive dependencies
4. match fixture consumers
5. add safe fallback for uncertain code areas
6. avoid broad whole-repository pytest execution

The target is substantial improvement over the 672.572 practice score and, more importantly, fewer than 6 hidden-batch misses.

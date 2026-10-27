"""CI security invariants and explicit environment quarantine behavior."""
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_PUBLIC_WORKFLOWS = [_ROOT / ".github/workflows" / name for name in ("ci.yml", "security.yml", "release.yml")]


@pytest.mark.parametrize("path", _PUBLIC_WORKFLOWS, ids=lambda p: p.name)
def test_workflow_security_invariants(path: Path) -> None:
    workflow = path.read_text(encoding="utf-8")
    actions = re.findall(r"^\s*-?\s*uses:\s*(\S+)", workflow, re.MULTILINE)
    assert actions
    unpinned = [action for action in actions if not re.fullmatch(r"[^@]+@[0-9a-fA-F]{40}", action)]
    assert not unpinned, f"Actions need full commit pins: {unpinned}"
    action_lines = [line for line in workflow.splitlines() if re.match(r"\s*-?\s*uses:", line)]
    assert all(re.search(r" # v\S+\s*$", line) for line in action_lines)
    assert re.search(r"^permissions:\s*\n  contents: read\s*\n", workflow, re.MULTILINE)
    assert "pull_request_target" not in workflow
    assert "write-all" not in workflow
    assert "persist-credentials: false" in workflow
    document = yaml.load(workflow, Loader=yaml.BaseLoader)
    scopes = [(None, document["permissions"])]
    scopes.extend((name, job.get("permissions", document["permissions"])) for name, job in document["jobs"].items())
    for name, grants in scopes:
        if path.name == "release.yml" and name == "attest-and-draft":
            assert grants == {"contents": "write", "id-token": "write", "attestations": "write"}
            continue
        assert grants.keys() <= {"contents", "actions", "security-events", "id-token"}
        assert grants.get("contents") == "read"
        assert grants.get("actions", "read") == "read"
        if path.name != "security.yml":
            assert "security-events" not in grants and "id-token" not in grants


def _ci_matrix(workflow: str) -> list[dict]:
    matrix = re.search(r"include: \$\{\{ fromJSON\('(.+)'\) \}\}", workflow)
    assert matrix is not None
    return json.loads(matrix[1])


def _assert_hosted_runners(workflow: str) -> None:
    targets = re.findall(r"(?m)^\s*runs-on:\s*(.+?)\s*$", workflow)
    assert targets
    for target in targets:
        runners = [leg["runner"] for leg in _ci_matrix(workflow)] if target == "${{ matrix.runner }}" else [target]
        assert all(isinstance(runner, str) and runner in {"ubuntu-latest", "windows-latest"}
                   for runner in runners), "Workflows must use hosted images"
    assert "runner.environment" not in workflow


def _assert_ci_triggers(workflow: str) -> None:
    triggers = yaml.load(workflow, Loader=yaml.BaseLoader)["on"]
    assert set(triggers) == {"push", "pull_request", "workflow_dispatch"}
    assert triggers["push"] == {"branches": ["main"]}


@pytest.mark.parametrize("path", _PUBLIC_WORKFLOWS, ids=lambda p: p.name)
def test_workflows_use_hosted_images_and_accept_pull_requests(path: Path) -> None:
    workflow = path.read_text(encoding="utf-8")
    _assert_hosted_runners(workflow)
    if path.name == "ci.yml":
        _assert_ci_triggers(workflow)
    elif path.name == "security.yml":
        triggers = yaml.load(workflow, Loader=yaml.BaseLoader)["on"]
        assert {"push", "pull_request"} <= set(triggers)
        assert triggers["push"]["branches"] == ["main"]
        assert set(re.findall(r"(?m)^\s*runs-on:\s*(.+?)\s*$", workflow)) == {"ubuntu-latest"}
    else:
        assert yaml.load(workflow, Loader=yaml.BaseLoader)["on"] == {
            "push": {"tags": ["v*"]}, "workflow_dispatch": "",
        }


@pytest.mark.parametrize("trigger", ["workflow_run", "issue_comment", "schedule"])
def test_ci_trigger_policy_rejects_extra_events(trigger: str) -> None:
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    workflow = workflow.replace("on:\n", f"on:\n  {trigger}:\n", 1)
    with pytest.raises(AssertionError):
        _assert_ci_triggers(workflow)


@pytest.mark.parametrize("push", ["  push:\n", "  push:\n    branches: [topic]\n"])
def test_ci_trigger_policy_rejects_branch_filter_on_pull_requests(push: str) -> None:
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    workflow = workflow.replace("  push:\n    branches: [main]\n", push, 1)
    workflow = workflow.replace("  pull_request:\n", "  pull_request:\n    branches: [main]\n", 1)
    with pytest.raises(AssertionError):
        _assert_ci_triggers(workflow)


@pytest.mark.parametrize("runner", ["custom-runner", '["Linux","X64","dedicated"]', "${{ vars.RUNNER }}"])
@pytest.mark.parametrize("path", _PUBLIC_WORKFLOWS, ids=lambda p: p.name)
def test_hosted_policy_rejects_other_runner_targets(path: Path, runner: str) -> None:
    workflow = re.sub(r"(?m)^(\s*runs-on:) .+$", lambda match: f"{match[1]} {runner}",
                      path.read_text(encoding="utf-8"))
    with pytest.raises(AssertionError, match="Workflows must use hosted images"):
        _assert_hosted_runners(workflow)


def assert_workflow_download_checksums(workflow: str) -> None:
    steps = {step["name"]: step for step in yaml.load(workflow, Loader=yaml.BaseLoader)["jobs"]["checks"]["steps"]}
    downloads = (
        ("Windows", "6f58ce889f59c311410f7d2b18895b33c03456463486f3b1ebc93d97a0f54541", "Expand-Archive"),
        ("Linux", "97ce978979194b5cf7e06a5e68020dbdaa7a4f3294c5452b6a1bc347100dbb79", "tar -xJf"),
    )
    for platform, checksum, extract in downloads:
        cache = steps[f"Cache {platform} ffmpeg archive"]
        install = steps[f"Install verified {platform} ffmpeg"]
        assert cache["if"] == install["if"] == f"runner.os == '{platform}'"
        assert cache["with"]["key"] == f"ffmpeg-{platform.lower()}-{checksum}"
        run = install["run"]
        assert f"$expected = '{checksum}'" in run
        assert "if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected)" in run
        assert "throw 'ffmpeg archive checksum mismatch'" in run
        assert run.index("Get-FileHash") < run.index(extract)


def test_workflow_download_checksums() -> None:
    assert_workflow_download_checksums((_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_ci_cancels_only_superseded_pull_requests() -> None:
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    concurrency = yaml.load(workflow, Loader=yaml.BaseLoader)["concurrency"]
    assert concurrency["group"] == "ci-${{ github.ref }}"
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def assert_ci_product_gates(workflow: str) -> None:
    assert "runs-on: ${{ matrix.runner }}" in workflow
    assert "fail-fast: false" in workflow
    assert "'.venv/Scripts/python.exe'" in workflow
    assert "'.venv/bin/python'" in workflow
    for command in ("scripts/bootstrap.py", "ruff check .", "pyright", "pytest -q -p no:cacheprovider", "npm test",
                    "npm run typecheck", "npm run build", "node --test", "tests/e2e/run.py"):
        steps = workflow.split("    steps:\n", 1)[1]
        matching = [step for step in steps.split("      - name:") if command in step]
        assert matching and all(not re.search(r"(?m)^        if:", step) for step in matching)
    assert workflow.index("npm run build") < workflow.index("tests/e2e/run.py")
    assert '--basetemp "$env:RUNNER_TEMP/pytest"' in workflow


def assert_ci_sbom_policy(workflow: str) -> None:
    steps = {step["name"]: step for step in yaml.load(workflow, Loader=yaml.BaseLoader)["jobs"]["checks"]["steps"]}
    sbom = steps["Source SBOMs and licence policy"]
    assert sbom["if"] == "runner.os == 'Windows'"
    for command in ("scripts/model_manifest.py", "scripts/sbom.py", "scripts/license_policy.py"):
        assert command in sbom["run"]
    upload = steps["Upload SBOMs"]
    assert upload["if"] == "${{ !cancelled() && runner.os == 'Windows' }}"
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["with"]["path"] == "dist/sbom/"
    assert upload["with"]["if-no-files-found"] == "error"


def test_ci_sbom_policy() -> None:
    assert_ci_sbom_policy((_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


def test_ci_runs_same_gates_and_browser_harness_on_both_platforms() -> None:
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert_ci_product_gates(workflow)
    jobs = workflow.split("\njobs:\n", 1)[1]
    assert re.findall(r"(?m)^  ([\w-]+):", jobs) == ["checks", "installer"]
    matrix = _ci_matrix(workflow)
    assert matrix == [{"os": "linux", "runner": "ubuntu-latest"},
                      {"os": "windows", "runner": "windows-latest"}]
    assert re.findall(r"(?m)^    if: (.+)$", jobs) == [
        "${{ !github.event.repository.private || vars.HOSTED_CI == 'true' }}",
        "${{ github.event_name == 'pull_request' && (!github.event.repository.private || vars.HOSTED_CI == 'true') }}",
    ]
    setup = workflow.split("- name: Set up Python", 1)[1].split("- name: Set up Node", 1)[0]
    assert "if:" not in setup
    assert "python-version-file: .python-version" in setup


def test_security_jobs_skip_private_repositories_and_use_scoped_permissions() -> None:
    workflow = (_ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8")
    sections = re.split(r"(?m)^  ([\w-]+):\s*$", workflow.split("\njobs:\n", 1)[1])
    jobs = dict(zip(sections[1::2], sections[2::2]))
    assert jobs.keys() == {"codeql", "dependency-review", "pip-audit", "osv-scanner", "gitleaks", "scorecard"}
    for job, body in jobs.items():
        guard = re.search(r"(?m)^    if: (.+)$", body)
        assert guard is not None
        assert guard[1].startswith("${{ !github.event.repository.private")
        assert guard[1].endswith(" }}")
        if job == "dependency-review":
            assert "github.event_name == 'pull_request'" in guard[1]
        assert ("security-events: write" in body) == (job in {"codeql", "osv-scanner", "scorecard"})
        assert "id-token: write" not in body
    assert "language: [python, javascript-typescript]" in jobs["codeql"]
    assert "queries: security-extended" in jobs["codeql"]
    assert "build-mode: none" in jobs["codeql"]
    assert "publish_results: false" in jobs["scorecard"]
    assert "sarif_file: results.sarif" in jobs["scorecard"]
    assert "uses: pypa/gh-action-pip-audit@" in jobs["pip-audit"]
    assert "require-hashes: true" in jobs["pip-audit"]
    assert "no-deps: true" in jobs["pip-audit"]
    assert "disable-pip" not in jobs["pip-audit"]
    assert "--config=osv-scanner.toml" in jobs["osv-scanner"]
    for lock in ("requirements-core.txt", "requirements-agent.txt", "requirements-dev.txt"):
        assert lock in jobs["pip-audit"]
        assert lock in jobs["osv-scanner"]
    for lock in ("ui/package-lock.json", "motion-core/package-lock.json"):
        assert lock in jobs["osv-scanner"]
    for tier in ("cpu", "nvidia"):
        assert f"--lockfile=requirements.txt:requirements-local-ai-{tier}.txt" in jobs["osv-scanner"]
    assert "gitleaks_8.30.1_linux_x64.tar.gz" in jobs["gitleaks"]
    assert "88f91962aa2f93ac6ab281d553b9e125f5197bbbce38f9f2437f7299c32e5509" in jobs["gitleaks"]
    assert "--redact" in jobs["gitleaks"]
    assert re.search(r"(?m)^  push:\s*\n    branches: \[main\]$", workflow)
    assert re.search(r"(?m)^  pull_request:$", workflow)
    assert re.search(r"(?m)^    - cron: '.+ \* \* 1'$", workflow)


def test_dependabot_updates_actions_and_npm_with_grouped_weekly_updates() -> None:
    config = (_ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
    updates = re.split(r"(?m)^  - package-ecosystem: ", config)[1:]
    assert len(updates) == 3
    expected = {("github-actions", "/"), ("npm", "/ui"), ("npm", "/motion-core")}
    observed = set()
    for update in updates:
        ecosystem = update.splitlines()[0]
        directory = re.search(r"(?m)^    directory: (.+)$", update)
        assert directory is not None
        observed.add((ecosystem, directory[1]))
        assert "interval: weekly" in update
        assert "update-types: [minor, patch]" in update
    assert observed == expected
    assert "pull_request_target" not in config


def _locked_versions() -> set[tuple[str, str]]:
    locked = set()
    for lock in _ROOT.glob("requirements-*.txt"):
        locked |= set(re.findall(r"(?m)^([A-Za-z0-9_.-]+)==(\S+)", lock.read_text(encoding="utf-8")))
    for lock in ("ui/package-lock.json", "motion-core/package-lock.json"):
        packages = json.loads((_ROOT / lock).read_text(encoding="utf-8"))["packages"]
        locked |= {(path.rsplit("node_modules/", 1)[-1], info["version"])
                   for path, info in packages.items() if path and "version" in info}
    return locked


def test_osv_exceptions_are_specific_reasoned_and_expiring() -> None:
    entries = tomllib.loads((_ROOT / "osv-scanner.toml").read_text(encoding="utf-8"))["IgnoredVulns"]
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids))
    locked = _locked_versions()
    for entry in entries:
        assert set(entry) == {"id", "ignoreUntil", "reason"}
        assert re.fullmatch(r"(GHSA(-[23456789cfghjmpqrvwx]{4}){3}|PYSEC-\d{4}-\d+)", entry["id"])
        assert isinstance(entry["ignoreUntil"], datetime.date)
        assert "docs/dependency-audit.md" in entry["reason"]
        # An exception covers the reviewed version only: a lock update must revisit it.
        scope = re.match(r"[^:]+: (\S+) (\S+)", entry["reason"])
        assert scope is not None
        assert (scope[1], scope[2].rstrip(".,")) in locked, f"{entry['id']}: review again for the new {scope[1]} version"


@pytest.mark.parametrize("path", _PUBLIC_WORKFLOWS, ids=lambda p: p.name)
def test_workflow_runner_context_only_in_steps(path: Path) -> None:
    workflow = path.read_text(encoding="utf-8")
    steps_indent = None
    in_step = False
    for number, line in enumerate(workflow.splitlines(), start=1):
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)
        if steps_indent is not None and indent <= steps_indent:
            steps_indent = None
            in_step = False
        if stripped == "steps:":
            steps_indent = indent
            in_step = False
        elif steps_indent is not None and stripped.startswith("- "):
            in_step = True
        if re.search(r"\$\{\{\s*runner\.", line):
            assert steps_indent is not None and in_step, f"runner context outside a step at line {number}"


@pytest.mark.parametrize("ci", ["true", "false"])
def test_quarantine_matches_only_exact_ids_in_ci(tmp_path: Path, ci: str) -> None:
    shutil.copyfile(_ROOT / "tests/conftest.py", tmp_path / "conftest.py")
    (tmp_path / "ci_quarantine.txt").write_text(
        "test_sample.py::test_sample[font] # Required system font is unavailable\n", encoding="utf-8",
    )
    (tmp_path / "test_sample.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('name', ['font', 'font-extra'])\n"
        "def test_sample(name):\n"
        "    assert name != 'font'\n", encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider"],
        cwd=tmp_path, env={**os.environ, "CI": ci}, capture_output=True, text=True, timeout=30,
    )
    if ci == "true":
        assert result.returncode == 0, result.stdout + result.stderr
        assert "1 passed, 1 skipped" in result.stdout
        assert "CI quarantine: Required system font is unavailable" in result.stdout
    else:
        assert result.returncode == 1, result.stdout + result.stderr
        assert "1 failed, 1 passed" in result.stdout
        assert "skipped" not in result.stdout


def test_quarantine_requires_a_reason(tmp_path: Path) -> None:
    shutil.copyfile(_ROOT / "tests/conftest.py", tmp_path / "conftest.py")
    (tmp_path / "ci_quarantine.txt").write_text("test_sample.py::test_sample\n", encoding="utf-8")
    (tmp_path / "test_sample.py").write_text("def test_sample():\n    pass\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path, env={**os.environ, "CI": "true"}, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 4, result.stdout + result.stderr
    assert "require a unique test id and ' # reason'" in result.stderr

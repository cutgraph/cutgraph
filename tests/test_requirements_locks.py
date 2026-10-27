"""Dependency locks retain input pins and require hashes for every artifact."""
from pathlib import Path
import hashlib
import re
import subprocess

import pytest

from scripts import lock_requirements


ROOT = Path(__file__).resolve().parents[1]
LOCKS = sorted(ROOT.glob("requirements-*.txt"))
PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s;]+)(?:\s*;.+)?\s*")


def _name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _entries(path: Path) -> list[str]:
    entries: list[str] = []
    pending = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pending += line.removesuffix("\\").rstrip() + " "
        if not line.endswith("\\"):
            entries.append(pending.strip())
            pending = ""
    assert not pending, f"unterminated continuation in {path.name}"
    return entries


def _input_pins(path: Path, ancestors: tuple[Path, ...] = ()) -> set[tuple[str, str]]:
    assert path not in ancestors, f"cyclic requirement include in {path.name}"
    pins: set[tuple[str, str]] = set()
    for entry in _entries(path):
        if entry.startswith("-r "):
            pins.update(_input_pins(path.parent / entry[3:].strip(), (*ancestors, path)))
        elif entry.startswith("-c "):
            assert (path.parent / entry[3:].strip()).is_file()
        else:
            match = PIN.fullmatch(entry)
            assert match, f"unpinned input in {path.name}: {entry}"
            pins.add((_name(match[1]), match[2]))
    return pins


def test_every_input_has_a_lock_and_comments() -> None:
    inputs = sorted(ROOT.glob("requirements-*.in"))
    assert inputs and LOCKS
    assert {path.stem for path in inputs} == {path.stem for path in LOCKS}
    assert ROOT / "requirements-reuse.txt" in LOCKS
    for path in inputs:
        assert any(line.startswith("#") for line in path.read_text(encoding="utf-8").splitlines())


@pytest.mark.parametrize("path", LOCKS, ids=lambda path: path.name)
def test_locks_pin_and_hash_every_package_and_preserve_input_versions(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "--extra-index-url" not in text
    assert "--index-url" not in text
    entries = _entries(path)
    assert entries
    versions: dict[str, set[str]] = {}
    for entry in entries:
        requirement, separator, hashes = entry.partition("--hash=")
        match = PIN.fullmatch(requirement)
        assert match, f"unpinned requirement in {path.name}: {requirement}"
        assert separator and re.fullmatch(
            r"sha256:[0-9a-f]{64}(?:\s+--hash=sha256:[0-9a-f]{64})*", hashes,
        ), f"missing or invalid hashes in {path.name}: {requirement}"
        versions.setdefault(_name(match[1]), set()).add(match[2])
    for name, version in _input_pins(path.with_suffix(".in")):
        assert versions.get(name) == {version}, f"input pin changed in {path.name}: {name}=={version}"


def test_ci_python_installs_require_hash_locked_sets() -> None:
    installs = []
    for workflow in (ROOT / ".github/workflows" / name for name in ("ci.yml", "security.yml", "release.yml")):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            if "pip install" in line:
                installs.append(line)
                assert "--require-hashes" in line
                locks = re.findall(r"-r (requirements-[\w-]+\.txt)", line)
                assert locks and all((ROOT / lock).is_file() for lock in locks)
    assert len(installs) == 5
    assert any("-r requirements-dev.txt" in line for line in installs)
    assert any("-r requirements-reuse.txt" in line for line in installs)
    assert any("--only-binary=:all: -r requirements-release.txt" in line for line in installs)


def test_release_lock_is_minimal_and_uses_reviewed_development_versions() -> None:
    def versions(path):
        return {_name(match[1]): match[2] for entry in _entries(path)
                if (match := PIN.fullmatch(entry.partition("--hash=")[0]))}

    release = versions(ROOT / "requirements-release.txt")
    development = versions(ROOT / "requirements-dev.txt")
    assert release.keys() == {"cryptography", "cffi", "pycparser"}
    assert release == {name: development[name] for name in release}
    assert "-c requirements-dev.txt" in (ROOT / "requirements-release.in").read_text()


@pytest.mark.parametrize("path", LOCKS, ids=lambda path: path.name)
def test_compile_commands_are_universal_and_use_explicit_indexes(path: Path) -> None:
    command = lock_requirements.compile_command("uv", path.with_suffix(".in"))
    source = f"{path.stem}/pyproject.toml" if lock_requirements.ADDON_LOCKS.get(path.stem) else path.with_suffix(".in").name
    assert command[:4] == ["uv", "pip", "compile", source]
    assert "--universal" in command and "--generate-hashes" in command
    assert command[command.index("--python-version") + 1] == "3.12"
    assert command[command.index("--output-file") + 1] == path.name
    assert command[command.index("--default-index") + 1] == "https://pypi.org/simple"
    assert "--extra-index-url" not in command
    assert "--torch-backend" not in command
    if lock_requirements.ADDON_LOCKS.get(path.stem):
        import tomllib

        project = tomllib.loads(lock_requirements.compile_project(path.with_suffix(".in")))
        assert set(project["tool"]["uv"]["sources"]) == {"torch", "torchaudio", "torchvision"}
        assert project["tool"]["uv"]["index"] == [{"name": "pytorch", "url": lock_requirements.ADDON_LOCKS[path.stem], "explicit": True}]
    else:
        assert "--index" not in command


def test_missing_uv_reports_actionable_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(lock_requirements.shutil, "which", lambda name: None)
    assert lock_requirements.main(["--check"]) == 1
    assert "uv must be installed and available on PATH" in capsys.readouterr().err


@pytest.mark.parametrize("check,drift", [(True, False), (True, True), (False, True)])
def test_regeneration_seeds_locks_and_check_leaves_inputs_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    check: bool, drift: bool,
) -> None:
    source = tmp_path / "requirements-core.in"
    source.write_text("# Keep this rationale\nexample==1.0\n", encoding="utf-8")
    lock = source.with_suffix(".txt")
    original = "example==1.0 --hash=sha256:" + "a" * 64 + "\n"
    generated = original.replace("a" * 64, "b" * 64) if drift else original
    lock.write_text(original, encoding="utf-8")
    monkeypatch.setattr(lock_requirements, "ROOT", tmp_path)
    monkeypatch.setattr(lock_requirements.shutil, "which", lambda name: "uv")

    def compile_lock(command: list[str], *, cwd: Path, check: bool, **_: object) -> subprocess.CompletedProcess[str]:
        assert cwd != tmp_path and not check
        assert (cwd / source.name).read_bytes() == source.read_bytes()
        output = cwd / command[command.index("--output-file") + 1]
        assert output.read_text(encoding="utf-8") == "example==1.0\n"  # seeded with versions, never hashes
        output.write_text(generated, encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(lock_requirements.subprocess, "run", compile_lock)
    assert lock_requirements.main(["--check"] if check else []) == int(check and drift)
    assert lock.read_text(encoding="utf-8") == (original if check else generated)
    assert source.read_text(encoding="utf-8") == "# Keep this rationale\nexample==1.0\n"
    output = capsys.readouterr().out
    if check and drift:
        assert "--- requirements-core.txt" in output
        assert "+++ requirements-core.txt (regenerated)" in output


def test_failed_compile_does_not_replace_any_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("agent", "core"):
        (tmp_path / f"requirements-{name}.in").write_text("example==1.0\n", encoding="utf-8")
        (tmp_path / f"requirements-{name}.txt").write_text("original\n", encoding="utf-8")
    monkeypatch.setattr(lock_requirements, "ROOT", tmp_path)
    monkeypatch.setattr(lock_requirements.shutil, "which", lambda name: "uv")

    def compile_lock(command: list[str], *, cwd: Path, check: bool, **_: object) -> subprocess.CompletedProcess[str]:
        output = cwd / command[command.index("--output-file") + 1]
        output.write_text("changed\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 7 if "requirements-core.in" in command else 0)

    monkeypatch.setattr(lock_requirements.subprocess, "run", compile_lock)
    assert lock_requirements.main([]) == 7
    assert all(path.read_text(encoding="utf-8") == "original\n" for path in tmp_path.glob("*.txt"))


def test_lock_check_never_seeds_hashes() -> None:
    """uv keeps hashes it finds in the output file, so a seeded hash would hide an edited one from --check."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("lock_requirements", ROOT / "scripts" / "lock_requirements.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    lock = (ROOT / "requirements-core.txt").read_text(encoding="utf-8")
    seeded = module.pins_only(lock)
    assert "--hash=" not in seeded
    assert "\\n" not in seeded
    assert all(pin in seeded for pin in ("fastapi==", "pillow==", "starlette=="))


@pytest.mark.parametrize("name", ["agent", "local-ai-cpu", "local-ai-nvidia"])
def test_addon_shared_pins_match_core(name: str) -> None:
    def versions(path):
        result: dict[str, set[str]] = {}
        for entry in _entries(path):
            if match := PIN.fullmatch(entry.partition("--hash=")[0]):
                result.setdefault(_name(match[1]), set()).add(match[2])
        return result

    core = versions(ROOT / "requirements-core.txt")
    addon = versions(ROOT / f"requirements-{name}.txt")
    assert core.keys() <= addon.keys()
    assert {key: addon[key] for key in core} == core
    assert "-c requirements-core.txt" in (ROOT / f"requirements-{name}.in").read_text()


def test_all_addons_agree_on_shared_non_core_packages():
    from pipeline.dependency_locks import TORCH_PACKAGES, requirements

    core = {name for name, _, _ in requirements((ROOT / "requirements-core.txt").read_text())}
    shared: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    for stem in lock_requirements.ADDON_LOCKS:
        names = set()
        for name, version, _ in requirements((ROOT / f"{stem}.txt").read_text()):
            if name not in core and name not in TORCH_PACKAGES:
                shared.setdefault(name, set()).add(version)
                names.add(name)
        for name in names:
            counts[name] = counts.get(name, 0) + 1
    assert {name for name in counts if counts[name] > 1} | core >= {"cffi", "pycparser", "torchcodec"}
    assert all(len(versions) == 1 for name, versions in shared.items() if counts[name] > 1)


@pytest.mark.parametrize("stem,digest,hooks", [
    ("local-ai-cpu", "d5234acf671d00b8b3cdcce5a552222ee81f8d987d0f77f1a350e97806f4f0bb",
     {"setuptools==84.0.0": {"distutils-precedence.pth"}}),
    ("local-ai-nvidia", "886f87a00dc9a1f6ff5fd600d0f05619f50b323f72e91f87519bbb399a957638",
     {"setuptools==84.0.0": {"distutils-precedence.pth"}}),
    ("agent", "a3037f7f5eb2d44c1d589aa5540868188b9daefc99fd0ff8ca5d81eca9fa7dc4",
     {"pywin32==312": {"pywin32.pth"}}),
])
def test_addon_locks_keep_the_reviewed_wheel_startup_hook_inventory(stem, digest, hooks):
    # A changed lock requires reviewing wheel RECORDs again before updating this inventory.
    lock = ROOT / f"requirements-{stem}.txt"
    assert hashlib.sha256(lock.read_bytes()).hexdigest() == digest, "Review wheel RECORDs for startup hooks."
    pins = {entry.partition(" ;")[0].partition(" --hash=")[0].strip() for entry in _entries(lock)}
    assert hooks.keys() <= pins
    allowed = {"pywin32.pth"} if stem == "agent" else {"distutils-precedence.pth"}
    assert {path for paths in hooks.values() for path in paths} == allowed


@pytest.mark.parametrize("stem", lock_requirements.ADDON_LOCKS)
def test_every_local_version_is_routed_to_its_exact_torch_index(stem):
    from pipeline.dependency_locks import PYPI_INDEX, TORCH_PACKAGES, package_index, requirements

    index = lock_requirements.ADDON_LOCKS[stem]
    for name, version, _ in requirements((ROOT / f"{stem}.txt").read_text()):
        assert package_index(name, version, index) == (index if name in TORCH_PACKAGES else PYPI_INDEX)
    assert package_index("torchcodec", "0.7.0", index) == PYPI_INDEX
    with pytest.raises(ValueError, match="no matching reviewed index"):
        package_index("torchcodec", "0.7.0+cu128", index)


@pytest.mark.parametrize("wrong_hash", [False, True])
def test_wheel_check_uses_only_metadata_and_checks_target_markers_hashes_and_indexes(
    monkeypatch, tmp_path, capsys, wrong_hash,
):
    import io
    import json

    digest = "a" * 64
    wrong = "b" * 64
    locks = {"requirements-local-ai-cpu": "https://download.pytorch.org/whl/cpu",
             "requirements-agent": None}
    (tmp_path / "requirements-local-ai-cpu.txt").write_text(
        f"torch==2.8.0+cpu --hash=sha256:{digest}\n"
        f"torchcodec==0.7.0 --hash=sha256:{digest}\n"
        f"linux-only==1.0 ; sys_platform == 'linux' --hash=sha256:{digest}\n")
    (tmp_path / "requirements-agent.txt").write_text(f"example==1.0 --hash=sha256:{digest}\n")
    requests = []
    def fetch(url, **kwargs):
        requests.append(url)
        assert not url.endswith(".whl")
        if url.startswith("https://download.pytorch.org/"):
            assert url == "https://download.pytorch.org/whl/cpu/torch/"
            return io.BytesIO(f'<a href="torch-2.8.0%2Bcpu-cp312-cp312-win_amd64.whl#sha256={digest}">wheel</a>'.encode())
        name, version = url.split("/")[-3:-1]
        assert name != "linux-only"
        filename = (f"{name}-{version}-cp312-cp312-win_amd64.whl" if name == "torchcodec"
                    else f"{name}-{version}-py3-none-any.whl")
        return io.BytesIO(json.dumps({"urls": [{"filename": filename, "digests": {
            "sha256": wrong if wrong_hash else digest}}]}).encode())
    monkeypatch.setattr(lock_requirements, "ROOT", tmp_path)
    monkeypatch.setattr(lock_requirements, "ADDON_LOCKS", locks)
    monkeypatch.setattr(lock_requirements, "urlopen", fetch)
    assert lock_requirements.main(["--check-wheels"]) == int(wrong_hash)
    assert len(requests) == 3
    output = capsys.readouterr().out
    assert ("1 passed, 2 failed" if wrong_hash else "3 passed, 0 failed") in output

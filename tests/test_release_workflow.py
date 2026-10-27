"""Release authority stays behind environment approval and away from pull requests."""
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_release_triggers_and_job_permissions_are_restricted():
    release = workflow("release.yml")
    assert release["on"] == {"push": {"tags": ["v*"]}, "workflow_dispatch": ""}
    assert not any(name.startswith("pull_request") for name in release["on"])
    assert release["permissions"] == {"contents": "read"}
    build, draft = release["jobs"]["build"], release["jobs"]["attest-and-draft"]
    assert set(release["jobs"]) == {"build", "attest-and-draft"}
    assert build["permissions"] == {"contents": "read"}
    assert "github.ref == 'refs/heads/main'" in build["if"]
    assert "HOSTED_CI" in build["if"]
    assert draft["permissions"] == {"contents": "write", "id-token": "write", "attestations": "write"}
    assert draft["needs"] == "build" and draft["environment"] == "release"
    assert "!github.event.repository.private" in draft["if"]
    assert "environment" not in build
    assert "secrets." not in str(build)


def test_every_release_action_is_pinned_and_checkout_discards_credentials():
    for job in workflow("release.yml")["jobs"].values():
        assert job["runs-on"] == "windows-latest"
        assert "permissions" not in str(job["steps"])
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w-]+/[\w-]+@[0-9a-f]{40}", step["uses"])
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] == "false"


def test_release_scripts_do_not_interpolate_expressions_and_token_is_draft_scoped():
    release = workflow("release.yml")
    for job in release["jobs"].values():
        assert "GH_TOKEN" not in job.get("env", {})
        for step in job["steps"]:
            assert "${{" not in step.get("run", "")
            if step["name"] == "Create draft release":
                assert step["env"] == {"GH_TOKEN": "${{ github.token }}"}
            else:
                assert "GH_TOKEN" not in str(step) and "github.token" not in str(step)
    assert "GH_TOKEN" not in release.get("env", {})


def test_release_version_tag_and_main_ancestry_are_bound_before_build():
    build = workflow("release.yml")["jobs"]["build"]
    steps = build["steps"]
    version = next(step for step in steps if step.get("id") == "version")
    run = version["run"]
    assert 'stable_version(v)' in run and '["project"]["version"]' in run
    assert '$env:GITHUB_REF_TYPE -eq \'tag\' -and $env:GITHUB_REF_NAME -ne "v$version"' in run
    assert 'git rev-parse --verify "refs/tags/v$version^{commit}"' in run
    assert "$tagCommit -ne $env:GITHUB_SHA" in run
    ancestry = "git merge-base --is-ancestor $env:GITHUB_SHA origin/main"
    assert ancestry in run
    assert "if ($LASTEXITCODE -ne 0) { throw 'Release commit must be an ancestor of origin/main' }" in run
    assert run.index(ancestry) < run.index('"version=$version" >> $env:GITHUB_OUTPUT')
    assert build["outputs"] == {"version": "${{ steps.version.outputs.version }}"}
    assert workflow("release.yml")["jobs"]["attest-and-draft"]["env"]["RELEASE_VERSION"] == "${{ needs.build.outputs.version }}"
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == "0"
    assert steps.index(version) < next(index for index, step in enumerate(steps) if "installer/build.py" in step.get("run", ""))


def test_draft_rechecks_remote_annotated_or_lightweight_tag_before_creation():
    run = workflow("release.yml")["jobs"]["attest-and-draft"]["steps"][-1]["run"]
    assert 'git ls-remote --exit-code origin "refs/tags/v$env:RELEASE_VERSION" "refs/tags/v$env:RELEASE_VERSION^{}"' in run
    assert "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }" in run
    peeled = '$tagCommit = $tagRefs["refs/tags/v$env:RELEASE_VERSION^{}"]'
    fallback = 'if (-not $tagCommit) { $tagCommit = $tagRefs["refs/tags/v$env:RELEASE_VERSION"] }'
    check = "if ($tagCommit -ne $env:GITHUB_SHA) { throw 'Release tag no longer identifies the built commit' }"
    assert run.index(peeled) < run.index(fallback) < run.index(check) < run.index("gh release create")


def test_privileged_install_uses_only_the_minimal_wheel_lock():
    draft = workflow("release.yml")["jobs"]["attest-and-draft"]
    installs = [step["run"].strip() for step in draft["steps"] if "pip install" in step.get("run", "")]
    assert installs == ["python -m pip install --require-hashes --only-binary=:all: -r requirements-release.txt"]


def test_workflow_reads_committed_support_policy_and_validates_build_identity():
    draft = workflow("release.yml")["jobs"]["attest-and-draft"]
    run = next(step["run"] for step in draft["steps"] if step["name"] == "Generate release manifest")
    assert '$receipt.version -ne $env:RELEASE_VERSION -or $receipt.sourceRevision -ne $env:GITHUB_SHA' in run
    assert '["tool"]["cutgraph"]["minimum-supported-version"]' in run
    assert '--minimum-version $minimumVersion' in run
    assert 'if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }' in run


def test_release_lifecycle_and_source_gates_match_ci():
    release = workflow("release.yml")
    assert release["concurrency"] == {"group": "release", "cancel-in-progress": "false"}
    build = {step["name"]: step for step in release["jobs"]["build"]["steps"]}
    assert build["Upload unsigned assets"]["with"]["retention-days"] == "30"
    install = build["Install locked dependencies"]["run"]
    assert "python -m venv .venv" in install and "'.venv/Scripts/python.exe'" in install
    assert '& $python -m pip install --require-hashes -r requirements-dev.txt' in install
    assert '"STUDIO_PYTHON=$python" >> $env:GITHUB_ENV' in install
    ci = {step["name"]: step for step in workflow("ci.yml")["jobs"]["checks"]["steps"]}
    assert build["Environment doctor"]["run"] == ci["Environment doctor"]["run"]
    assert build["Source SBOMs and licence policy"]["run"] == ci["Source SBOMs and licence policy"]["run"]
    for name in ("Python tests", "Python lint", "Python types", "Environment doctor", "Browser e2e"):
        assert build[name]["run"].startswith("& $env:STUDIO_PYTHON ")


def test_download_verification_uses_an_independent_repository_and_tag_rules_are_documented():
    installing = (ROOT / "docs/installing.md").read_text(encoding="utf-8")
    assert "official repository named in the product\nREADME or website" in installing
    assert "independently of the download page" in installing
    development = (ROOT / "installer/README.md").read_text(encoding="utf-8")
    assert "`v*` tag ruleset" in development and "preventing updates and deletion" in development


def test_release_gates_sign_verify_hash_attest_and_create_only_a_draft():
    release = workflow("release.yml")
    build = {step["name"]: step for step in release["jobs"]["build"]["steps"]}
    for command in ("pytest", "ruff", "pyright", "ui test", "ui run typecheck", "ui run build", "node --test",
                    "tests/e2e/run.py", "installer/build.py", "installer/launcher/smoke.py",
                    "--i-am-on-a-disposable-machine", "installer/release_assets.py collect"):
        assert any(command in step.get("run", "") for step in build.values())
    smoke = build["Bundle smoke using Media Foundation"]
    assert smoke["env"] == {"CUTGRAPH_TEST_ENCODERS": "h264_mf"}
    assert "--expected-encoder h264_mf" in smoke["run"]
    draft = release["jobs"]["attest-and-draft"]["steps"]
    names = [step["name"] for step in draft]
    ordered = ["Sign release manifest", "Verify signature and installer hash", "Hash every release asset",
               "Attest every asset including checksums", "Create draft release"]
    assert [names.index(name) for name in ordered] == sorted(names.index(name) for name in ordered)
    sign = draft[names.index("Sign release manifest")]
    assert sign["env"] == {"RELEASE_ED25519_KEY": "${{ secrets.RELEASE_ED25519_KEY }}"}
    assert sum("secrets." in str(step) for step in draft) == 1
    assert "--draft" in draft[-1]["run"] and "--verify-tag" in draft[-1]["run"]
    assert draft[names.index("Attest every asset including checksums")]["with"]["subject-path"].endswith("/assets/*")


def test_complete_asset_set_is_uploaded_hashed_attested_and_attached():
    jobs = workflow("release.yml")["jobs"]
    build = jobs["build"]["steps"]
    collection = next(index for index, step in enumerate(build)
                      if "installer/release_assets.py collect" in step.get("run", ""))
    upload = next(index for index, step in enumerate(build)
                  if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert collection < upload
    assert build[upload]["with"]["path"] == "${{ runner.temp }}/assets/*"
    draft = jobs["attest-and-draft"]["steps"]
    download = next(step for step in draft if step.get("uses", "").startswith("actions/download-artifact@"))
    assert download["with"]["name"] == build[upload]["with"]["name"]
    assert download["with"]["path"] == "${{ runner.temp }}/assets"
    hashes = next(step for step in draft if step["name"] == "Hash every release asset")
    assert 'installer/release_assets.py checksums "$env:RUNNER_TEMP/assets"' in hashes["run"]
    attestation = next(step for step in draft if step["name"] == "Attest every asset including checksums")
    assert attestation["with"]["subject-path"] == "${{ runner.temp }}/assets/*"
    publish = draft[-1]["run"]
    assert 'Get-ChildItem -LiteralPath "$env:RUNNER_TEMP/assets" -File' in publish
    assert 'gh release create "v$env:RELEASE_VERSION" @assets' in publish


def test_authenticode_seam_stays_disabled_without_signing_tools_or_targets():
    jobs = workflow("release.yml")["jobs"]
    seams = [step for job in jobs.values() for step in job["steps"]
             if step["name"] == "Authenticode signing seam (disabled)"]
    assert seams == [{"name": "Authenticode signing seam (disabled)",
                      "if": "${{ success() && false }}",
                      "run": "throw 'Configure Authenticode in the builder before enabling this seam.'"}]
    for job in jobs.values():
        for step in job["steps"]:
            assert "signing" not in step.get("uses", "").lower()
            assert not re.search(r"\b(?:signtool|azuresigntool|osslsigncode|set-authenticodesignature)\b",
                                 step.get("run", ""), re.IGNORECASE)


def test_setup_and_uninstaller_have_no_enabled_signing_configuration():
    script = (ROOT / "installer/cutgraph.iss").read_text(encoding="utf-8")
    directives = {line.split("=", 1)[0].strip().lower() for line in script.splitlines()
                  if "=" in line and not line.lstrip().startswith(";")}
    assert not {"signtool", "signtoolrunminimized", "signeduninstaller", "signeduninstallerdir"} & directives


def test_pull_request_installer_has_no_environment_or_secrets():
    installer = workflow("ci.yml")["jobs"]["installer"]
    assert installer["runs-on"] == "windows-latest"
    assert "github.event_name == 'pull_request'" in installer["if"] and "HOSTED_CI" in installer["if"]
    assert installer["permissions"] == {"contents": "read"}
    assert "environment" not in installer and "secrets." not in str(installer)
    assert any("installer/build.py --installer" in step.get("run", "") for step in installer["steps"])


def test_compiler_is_checked_before_execution_and_requires_hosted_runner():
    script = (ROOT / "installer/prepare_compiler.ps1").read_text(encoding="utf-8")
    assert "compiler.json" in script and ").release" in script
    assert "$pin.url" in script and "$pin.sha256" in script
    assert script.index("Get-FileHash") < script.index("Start-Process")
    assert "RUNNER_ENVIRONMENT -ne 'github-hosted'" in script

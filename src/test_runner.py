"""Test Runner — clones the repo, applies the PR branch, and runs the test suite."""

import asyncio
import logging
import os
import shutil
import stat
import time

import git

from src.config import settings

logger = logging.getLogger(__name__)


def _remove_readonly(func, path, _exc_info):
    """Best-effort handler for read-only files on Windows during rmtree."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _prepare_workspace(workspace: str) -> str:
    """Create a clean workspace path, tolerating Windows file-lock races."""
    if not os.path.exists(workspace):
        os.makedirs(workspace, exist_ok=True)
        return workspace

    for _ in range(3):
        try:
            shutil.rmtree(workspace, onerror=_remove_readonly)
            break
        except PermissionError:
            time.sleep(0.5)
    else:
        # Fall back to a fresh path if old workspace is still locked by another process.
        workspace = f"{workspace}-{int(time.time())}"
        logger.warning("Workspace cleanup locked; using fallback path: %s", workspace)

    if not os.path.exists(workspace):
        os.makedirs(workspace, exist_ok=True)
    return workspace


def _clone_repo(clone_url: str, head_branch: str, workspace: str) -> str:
    """Clone the repository and checkout the PR branch. Returns the repo path."""
    # Inject token for private repos
    authed_url = clone_url.replace(
        "https://", f"https://x-access-token:{settings.github_token}@"
    )

    logger.info("Cloning %s (branch: %s) into %s", clone_url, head_branch, workspace)
    repo = git.Repo.clone_from(authed_url, workspace, branch=head_branch, depth=50)
    logger.info("Clone complete — HEAD: %s", repo.head.commit.hexsha[:10])
    return workspace


async def _run_command(cmd: str, cwd: str, timeout: int) -> dict:
    """Run a shell command asynchronously with a timeout."""
    logger.info("Running: %s (cwd=%s, timeout=%ds)", cmd, cwd, timeout)
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return {
            "command": cmd,
            "exit_code": proc.returncode,
            "stdout": stdout.decode(errors="replace")[-5000:],  # Last 5 KB
            "stderr": stderr.decode(errors="replace")[-5000:],
            "timed_out": False,
        }
    except asyncio.TimeoutError:
        proc.kill()
        return {
            "command": cmd,
            "exit_code": -1,
            "stdout": "",
            "stderr": "Command timed out",
            "timed_out": True,
        }


def _detect_test_commands(repo_path: str) -> list[str]:
    """Auto-detect the test framework and return commands to run."""
    commands = []

    # Python
    if os.path.exists(os.path.join(repo_path, "pyproject.toml")) or \
       os.path.exists(os.path.join(repo_path, "setup.py")):
        commands.append("pip install -e '.[dev]' 2>&1 && python -m pytest --tb=short -q 2>&1")

    if os.path.exists(os.path.join(repo_path, "requirements.txt")):
        commands.insert(0, "pip install -r requirements.txt 2>&1")

    # Node.js
    if os.path.exists(os.path.join(repo_path, "package.json")):
        commands.append("npm ci 2>&1 && npm test 2>&1")

    # Go
    if os.path.exists(os.path.join(repo_path, "go.mod")):
        commands.append("go test ./... 2>&1")

    # Rust
    if os.path.exists(os.path.join(repo_path, "Cargo.toml")):
        commands.append("cargo test 2>&1")

    # Java / Maven
    if os.path.exists(os.path.join(repo_path, "pom.xml")):
        commands.append("mvn test 2>&1")

    # Java / Gradle
    if os.path.exists(os.path.join(repo_path, "build.gradle")) or \
       os.path.exists(os.path.join(repo_path, "build.gradle.kts")):
        commands.append("./gradlew test 2>&1")

    # Makefile fallback
    if not commands and os.path.exists(os.path.join(repo_path, "Makefile")):
        commands.append("make test 2>&1")

    return commands


async def clone_and_run_tests(pr_info: dict) -> dict:
    """Full pipeline: clone → detect tests → install deps → run tests."""
    workspace = os.path.join(
        settings.workdir,
        pr_info["repo_full_name"].replace("/", "_"),
        f"pr-{pr_info['pr_number']}",
    )

    # Clean up any previous run with Windows-safe retries.
    workspace = _prepare_workspace(workspace)

    repo_path = _clone_repo(pr_info["clone_url"], pr_info["head_branch"], workspace)

    test_commands = _detect_test_commands(repo_path)
    if not test_commands:
        logger.warning("No test framework detected for %s", pr_info["repo_full_name"])
        return {
            "repo_path": repo_path,
            "tests_found": False,
            "results": [],
        }

    results = []
    for cmd in test_commands:
        result = await _run_command(cmd, repo_path, settings.test_timeout_seconds)
        results.append(result)
        logger.info("Command '%s' exited with code %d", cmd, result["exit_code"])

    all_passed = all(r["exit_code"] == 0 for r in results)
    logger.info("Test run complete — all passed: %s", all_passed)

    return {
        "repo_path": repo_path,
        "tests_found": True,
        "all_passed": all_passed,
        "results": results,
    }

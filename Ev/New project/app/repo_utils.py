from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests


UTILS_REPO_URL = "https://github.com/bidgely/Utils.git"
UTILS_REPO_API = "https://api.github.com/repos/bidgely/Utils/pulls"
UTILS_DEST_ROOT = Path("prod-one-time-scripts/rds")


@dataclass(frozen=True)
class RepoExportSummary:
    repo_path: Path
    branch_name: str
    destination_dir: Path
    copied_files: list[Path]
    commit_created: bool
    pr_created: bool
    pr_url: str | None


def _run_git(args: list[str], repo_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_command(args: list[str], repo_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=repo_path,
        text=True,
        capture_output=True,
        check=False,
    )


def _month_day_label(date_str: str) -> str:
    parsed = datetime.strptime(date_str, "%Y%m%d")
    return f"{parsed.strftime('%B')}{parsed.day}"


def build_branch_name(pilot_name: str, date_str: str) -> str:
    return f"{pilot_name.upper()}_Config_Push_Retry_{_month_day_label(date_str)}"


def _remote_branch_exists(repo_path: Path, branch_name: str) -> bool:
    result = _run_git(["ls-remote", "--heads", "origin", branch_name], repo_path)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return bool(result.stdout.strip())


def choose_available_branch_name(repo_path: Path, base_branch_name: str) -> str:
    if not _remote_branch_exists(repo_path, base_branch_name):
        return base_branch_name

    suffix = 2
    while True:
        candidate = f"{base_branch_name}_Run{suffix}"
        if not _remote_branch_exists(repo_path, candidate):
            return candidate
        suffix += 1


def ensure_utils_repo(repo_path: Path) -> Path:
    if repo_path.exists():
        git_dir = repo_path / ".git"
        if not git_dir.exists():
            raise ValueError(f"Existing path is not a git repo: {repo_path}")
        return repo_path

    clone_result = _run_command(["git", "clone", UTILS_REPO_URL, str(repo_path)], repo_path=Path.cwd())
    if clone_result.returncode != 0:
        raise RuntimeError(f"Failed to clone Utils repo: {clone_result.stderr.strip()}")
    return repo_path


def _ensure_clean_repo(repo_path: Path) -> None:
    status_result = _run_git(["status", "--porcelain"], repo_path)
    if status_result.returncode != 0:
        raise RuntimeError(status_result.stderr.strip() or status_result.stdout.strip())
    if status_result.stdout.strip():
        raise RuntimeError(
            "Utils repo has uncommitted changes. Please commit/stash them before running repo export."
        )


def update_repo_to_latest_master(repo_path: Path) -> str:
    _ensure_clean_repo(repo_path)

    fetch_result = _run_git(["fetch", "origin"], repo_path)
    if fetch_result.returncode != 0:
        raise RuntimeError(fetch_result.stderr.strip() or fetch_result.stdout.strip())

    base_branch = "master"

    checkout_master = _run_git(["checkout", base_branch], repo_path)
    if checkout_master.returncode != 0:
        raise RuntimeError(checkout_master.stderr.strip() or checkout_master.stdout.strip())

    reset_result = _run_git(["reset", "--hard", f"origin/{base_branch}"], repo_path)
    if reset_result.returncode != 0:
        raise RuntimeError(reset_result.stderr.strip() or reset_result.stdout.strip())

    return base_branch


def checkout_branch_from_base(repo_path: Path, branch_name: str, base_branch: str) -> None:
    checkout_branch = _run_git(["checkout", "-B", branch_name, base_branch], repo_path)
    if checkout_branch.returncode != 0:
        raise RuntimeError(checkout_branch.stderr.strip() or checkout_branch.stdout.strip())


def copy_scripts_to_utils_repo(*, repo_path: Path, scripts_dir: Path, date_str: str) -> tuple[Path, list[Path]]:
    if not scripts_dir.exists():
        raise ValueError(f"Scripts directory does not exist: {scripts_dir}")

    destination_dir = repo_path / UTILS_DEST_ROOT / date_str
    destination_dir.mkdir(parents=True, exist_ok=True)

    copied_files: list[Path] = []
    for script_path in sorted(scripts_dir.glob("*.sh")):
        destination_path = destination_dir / script_path.name
        shutil.copy2(script_path, destination_path)
        copied_files.append(destination_path)

    if not copied_files:
        raise ValueError(f"No .sh files found in {scripts_dir}")

    return destination_dir, copied_files


def git_add_commit_push(
    *,
    repo_path: Path,
    branch_name: str,
    destination_dir: Path,
    commit_message: str,
    push: bool,
) -> bool:
    add_result = _run_git(["add", str(destination_dir)], repo_path)
    if add_result.returncode != 0:
        raise RuntimeError(add_result.stderr.strip() or add_result.stdout.strip())

    diff_result = _run_git(["diff", "--cached", "--quiet"], repo_path)
    if diff_result.returncode == 0:
        return False
    if diff_result.returncode not in (0, 1):
        raise RuntimeError(diff_result.stderr.strip() or diff_result.stdout.strip())

    commit_result = _run_git(["commit", "-m", commit_message], repo_path)
    if commit_result.returncode != 0:
        raise RuntimeError(commit_result.stderr.strip() or commit_result.stdout.strip())

    if push:
        push_result = _run_git(["push", "-u", "origin", branch_name], repo_path)
        if push_result.returncode != 0:
            raise RuntimeError(push_result.stderr.strip() or push_result.stdout.strip())

    return True


def maybe_create_pr(*, repo_path: Path, branch_name: str, title: str, body: str, create_pr: bool) -> tuple[bool, str | None]:
    if not create_pr:
        return False, None

    gh_check = _run_command(["which", "gh"], repo_path)
    if gh_check.returncode == 0:
        pr_result = _run_command(
            ["gh", "pr", "create", "--title", title, "--body", body, "--head", branch_name],
            repo_path,
        )
        if pr_result.returncode == 0:
            pr_url = pr_result.stdout.strip().splitlines()[-1].strip() if pr_result.stdout.strip() else None
            return True, pr_url

    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        raise RuntimeError(
            "GitHub CLI PR creation is unavailable and GITHUB_TOKEN is not set for API fallback."
        )

    response = requests.post(
        UTILS_REPO_API,
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "title": title,
            "head": branch_name,
            "base": "master",
            "body": body,
        },
        timeout=30,
    )
    if response.status_code >= 400:
        try:
            error_payload = response.json()
        except ValueError:
            error_payload = response.text
        raise RuntimeError(f"GitHub API PR creation failed: {error_payload}")

    response_payload = response.json()
    return True, response_payload.get("html_url")


def export_scripts_to_utils_repo(
    *,
    pilot_name: str,
    date_str: str,
    repo_path: Path,
    scripts_dir: Path,
    create_pr: bool,
    push: bool,
) -> RepoExportSummary:
    repo_path = ensure_utils_repo(repo_path)
    branch_name = choose_available_branch_name(repo_path, build_branch_name(pilot_name, date_str))
    commit_message = branch_name.replace("_", " ")
    pr_title = commit_message
    pr_body = f"Adds generated {pilot_name.upper()} retry scripts for {date_str}."

    print(f"[{pilot_name.upper()}] Step 1/4: updating Utils repo at {repo_path} from latest origin/master...")
    base_branch = update_repo_to_latest_master(repo_path)
    checkout_branch_from_base(repo_path, branch_name, base_branch)

    print(f"[{pilot_name.upper()}] Step 2/4: copying generated scripts into {UTILS_DEST_ROOT / date_str}...")
    destination_dir, copied_files = copy_scripts_to_utils_repo(
        repo_path=repo_path,
        scripts_dir=scripts_dir,
        date_str=date_str,
    )

    print(f"[{pilot_name.upper()}] Step 3/4: creating commit on branch {branch_name}...")
    commit_created = git_add_commit_push(
        repo_path=repo_path,
        branch_name=branch_name,
        destination_dir=destination_dir,
        commit_message=commit_message,
        push=push or create_pr,
    )

    print(f"[{pilot_name.upper()}] Step 4/4: creating PR..." if create_pr else f"[{pilot_name.upper()}] Step 4/4: PR creation skipped.")
    pr_created, pr_url = maybe_create_pr(
        repo_path=repo_path,
        branch_name=branch_name,
        title=pr_title,
        body=pr_body,
        create_pr=create_pr,
    )

    return RepoExportSummary(
        repo_path=repo_path,
        branch_name=branch_name,
        destination_dir=destination_dir,
        copied_files=copied_files,
        commit_created=commit_created,
        pr_created=pr_created,
        pr_url=pr_url,
    )

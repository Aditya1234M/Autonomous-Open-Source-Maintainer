"""PR Browser — uses Nova Act to navigate and extract PR details from GitHub."""

import logging
import os
from urllib.parse import urlparse

from github import Github
from nova_act import NovaAct

from src.config import settings

logger = logging.getLogger(__name__)


async def browse_pr(pr_url: str) -> dict:
    """Use Nova Act to browse a GitHub PR and extract structured information.

    Returns a dict with:
        - title, description, file_changes, comments, ci_status

    Uses IAM-based authentication via AWS credentials — no Nova Act API key needed.
    """
    logger.info("Browsing PR: %s", pr_url)

    # NovaAct picks up IAM credentials from environment.
    os.environ["AWS_ACCESS_KEY_ID"] = settings.aws_access_key_id
    os.environ["AWS_SECRET_ACCESS_KEY"] = settings.aws_secret_access_key
    os.environ["AWS_DEFAULT_REGION"] = settings.aws_region
    os.environ["AWS_REGION"] = settings.aws_region

    try:
        with NovaAct(
            starting_page=pr_url,
        ) as nova:
            # Extract PR title and description
            result = nova.act(
                "Read the pull request title and description. "
                "Return them as JSON with keys 'title' and 'description'."
            )
            pr_meta = result.parsed_response if result.success else {"title": "", "description": ""}

            # Navigate to the Files Changed tab and extract changed files
            result = nova.act(
                "Click on the 'Files changed' tab. "
                "List all changed file paths and for each file, extract the diff. "
                "Return as JSON: a list of objects with keys 'file_path' and 'diff'."
            )
            file_changes = result.parsed_response if result.success else []

            # Check CI status
            result = nova.act(
                "Go back to the Conversation tab. "
                "Check if there are any CI/CD status checks shown. "
                "Return JSON with key 'ci_status'. "
                "Allowed values: 'passing', 'failing', or 'pending'."
            )
            ci_info = result.parsed_response if result.success else {"ci_status": "unknown"}

            # Read review comments if any
            result = nova.act(
                "Read any existing review comments on this PR. "
                "Return as JSON: a list of objects with keys 'author', 'body', and 'file_path'."
            )
            comments = result.parsed_response if result.success else []

        return {
            "title": pr_meta.get("title", ""),
            "description": pr_meta.get("description", ""),
            "file_changes": file_changes,
            "ci_status": ci_info.get("ci_status", "unknown"),
            "existing_comments": comments,
        }
    except Exception as exc:
        logger.warning("Nova Act unavailable, falling back to GitHub API: %s", exc)
        return _browse_pr_via_github_api(pr_url)


def _browse_pr_via_github_api(pr_url: str) -> dict:
    """Fallback PR extraction via GitHub API when Nova Act cannot initialize."""
    owner, repo_name, pr_number = _parse_pr_url(pr_url)

    gh = Github(settings.github_token)
    repo = gh.get_repo(f"{owner}/{repo_name}")
    pr = repo.get_pull(pr_number)

    file_changes = []
    for changed in pr.get_files():
        file_changes.append({
            "file_path": changed.filename,
            "diff": changed.patch or "",
        })

    comments = []
    for c in pr.get_issue_comments():
        comments.append({
            "author": c.user.login if c.user else "unknown",
            "body": c.body or "",
            "file_path": None,
        })
    for c in pr.get_review_comments():
        comments.append({
            "author": c.user.login if c.user else "unknown",
            "body": c.body or "",
            "file_path": c.path,
        })

    ci_status = "unknown"
    status = repo.get_commit(pr.head.sha).get_combined_status().state
    if status == "success":
        ci_status = "passing"
    elif status == "failure":
        ci_status = "failing"
    elif status == "pending":
        ci_status = "pending"

    return {
        "title": pr.title or "",
        "description": pr.body or "",
        "file_changes": file_changes,
        "ci_status": ci_status,
        "existing_comments": comments,
    }


def _parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    """Parse a GitHub PR URL into owner, repo, and PR number."""
    parsed = urlparse(pr_url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 4 or parts[2] != "pull":
        raise ValueError(f"Unsupported PR URL format: {pr_url}")
    return parts[0], parts[1], int(parts[3])

"""PR Browser — uses Nova Act to navigate and extract PR details from GitHub."""

import logging

from nova_act import NovaAct

from src.config import settings

logger = logging.getLogger(__name__)


async def browse_pr(pr_url: str) -> dict:
    """Use Nova Act to browse a GitHub PR and extract structured information.

    Returns a dict with:
        - title, description, file_changes, comments, ci_status
    """
    logger.info("Browsing PR: %s", pr_url)

    with NovaAct(
        starting_page=pr_url,
        api_key=settings.nova_act_api_key,
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
            "Return JSON with key 'ci_status' whose value is 'passing', 'failing', or 'pending'."
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

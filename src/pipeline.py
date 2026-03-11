"""Pipeline orchestrator — ties together browsing, cloning, analysis, testing, and reviewing."""

import logging

from src.analyzer import analyze_codebase_with_pr
from src.pr_browser import browse_pr
from src.reviewer import post_review
from src.test_runner import clone_and_run_tests

logger = logging.getLogger(__name__)


async def review_pull_request(pr_info: dict) -> None:
    """End-to-end review pipeline for a single PR.

    Steps:
        1. Browse the PR with Nova Act to extract metadata and diffs
        2. Clone the repo and run existing tests
        3. Send full codebase + diff to Nova 2 Pro for deep analysis
        4. Post the review back to GitHub
    """
    repo = pr_info["repo_full_name"]
    pr_num = pr_info["pr_number"]
    logger.info("=== Starting review pipeline for %s PR #%d ===", repo, pr_num)

    # Step 1: Browse PR with Nova Act
    logger.info("[1/4] Browsing PR with Nova Act…")
    pr_details = await browse_pr(pr_info["pr_url"])

    # Step 2: Clone repo and run tests
    logger.info("[2/4] Cloning repo and running tests…")
    test_results = await clone_and_run_tests(pr_info)

    # Step 3: Analyze codebase with Nova 2 Pro
    logger.info("[3/4] Analyzing codebase with Nova 2 Pro…")
    diff_summary = _format_diff_summary(pr_details)
    analysis = await analyze_codebase_with_pr(test_results["repo_path"], diff_summary)

    # Step 4: Post review to GitHub
    logger.info("[4/4] Posting review to GitHub…")
    await post_review(pr_info, analysis, test_results)

    logger.info("=== Review pipeline complete for %s PR #%d ===", repo, pr_num)


def _format_diff_summary(pr_details: dict) -> str:
    """Format the PR details into a diff summary string for the analyzer."""
    parts = [
        f"PR Title: {pr_details.get('title', 'N/A')}",
        f"PR Description: {pr_details.get('description', 'N/A')}",
        f"CI Status: {pr_details.get('ci_status', 'unknown')}",
        "",
        "Changed Files:",
    ]

    for change in pr_details.get("file_changes", []):
        parts.append(f"\n--- {change.get('file_path', 'unknown')} ---")
        parts.append(change.get("diff", ""))

    return "\n".join(parts)

"""GitHub Reviewer — posts review comments and fix suggestions back to the PR."""

import logging

from github import Github
from github.GithubException import GithubException

from src.config import settings

logger = logging.getLogger(__name__)


def _build_review_body(analysis: dict, test_results: dict) -> str:
    """Compose the review comment body from the analysis and test results."""
    sections = []

    # Header
    risk = analysis.get("risk_level", "unknown")
    risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(risk, "⚪")
    header = f"## 🤖 Autonomous Maintainer Review\n\n**Risk Level:** {risk_emoji} {risk.upper()}"
    sections.append(header)

    # Summary
    summary = analysis.get("summary", "No summary available.")
    sections.append(f"### Summary\n{summary}")

    # Test Results
    if test_results.get("tests_found"):
        status = "✅ All tests passed" if test_results.get("all_passed") else "❌ Some tests failed"
        sections.append(f"### Test Results\n{status}")

        for r in test_results.get("results", []):
            if r["exit_code"] != 0:
                sections.append(
                    f"**`{r['command']}`** — exit code {r['exit_code']}\n"
                    f"```\n{r['stderr'][-2000:]}\n```"
                )
    else:
        sections.append("### Test Results\n⚠️ No test framework detected in this repository.")

    # Issues found
    issues = analysis.get("issues", [])
    if issues:
        sections.append("### Issues Found")
        for i, issue in enumerate(issues, 1):
            sev = issue.get("severity", "info")
            sections.append(
                f"**{i}. [{sev.upper()}]** `{issue.get('file', '?')}` "
                f"(line {issue.get('line', '?')})\n"
                f"> {issue.get('description', '')}\n\n"
                f"**Suggested fix:** {issue.get('suggestion', 'N/A')}"
            )

    # Missing tests
    missing = analysis.get("missing_tests", [])
    if missing:
        sections.append("### Missing Tests")
        for t in missing:
            sections.append(f"- {t}")

    sections.append(
        "\n---\n*This review was generated automatically by "
        "[Autonomous Open Source Maintainer](https://github.com/your-org/autonomous-maintainer). "
        "Please verify suggestions before merging.*"
    )

    return "\n\n".join(sections)


async def post_review(pr_info: dict, analysis: dict, test_results: dict) -> None:
    """Post a review on the GitHub PR with the analysis results."""
    g = Github(settings.github_token)
    repo = g.get_repo(pr_info["repo_full_name"])
    pr = repo.get_pull(pr_info["pr_number"])

    body = _build_review_body(analysis, test_results)

    # Determine the review event type
    approval = analysis.get("approval", "comment")
    event_map = {
        "approve": "APPROVE",
        "request_changes": "REQUEST_CHANGES",
        "comment": "COMMENT",
    }
    event = event_map.get(approval, "COMMENT")

    # GitHub does not allow requesting changes on your own PR.
    viewer_login = g.get_user().login
    pr_author_login = pr.user.login if pr.user else ""
    if viewer_login == pr_author_login and event in {"REQUEST_CHANGES", "APPROVE"}:
        logger.info(
            "Reviewer is PR author (%s); using COMMENT instead of %s",
            viewer_login,
            event,
        )
        event = "COMMENT"

    # Build inline comments for specific issues
    inline_comments = []
    commit = repo.get_commit(pr_info["head_sha"])

    for issue in analysis.get("issues", []):
        file_path = issue.get("file")
        line = issue.get("line")
        if file_path and line:
            inline_comments.append({
                "path": file_path,
                "position": line,  # Note: this is diff position, may need mapping
                "body": (
                    f"**[{issue.get('severity', 'info').upper()}]** "
                    f"{issue.get('description', '')}\n\n"
                    f"**Suggestion:** {issue.get('suggestion', 'N/A')}"
                ),
            })

    # Post the review. If token cannot create reviews (403), fallback to PR comment.
    try:
        if inline_comments:
            pr.create_review(
                commit=commit,
                body=body,
                event=event,
                comments=inline_comments,
            )
        else:
            pr.create_review(
                commit=commit,
                body=body,
                event=event,
            )

        logger.info(
            "Posted %s review on %s PR #%d with %d inline comments",
            event, pr_info["repo_full_name"], pr_info["pr_number"], len(inline_comments),
        )
    except GithubException as exc:
        unprocessable_review = exc.status == 422 and "unprocessable" in str(exc).lower()
        if unprocessable_review:
            logger.warning(
                "Review payload rejected by GitHub (422). Falling back to issue comment."
            )
            fallback_note = (
                "\n\n_Note: structured review was rejected by GitHub, "
                "so this was posted as a PR comment._"
            )
            pr.create_issue_comment(
                body + fallback_note
            )
            return

        forbidden_review = exc.status == 403 and "personal access token" in str(exc).lower()
        if not forbidden_review:
            raise

        logger.warning(
            "Token cannot create pull request reviews (403). Falling back to issue comment."
        )
        fallback_note = (
            "\n\n_Note: review API permission missing for current token; "
            "posted as PR comment instead._"
        )
        pr.create_issue_comment(
            body + fallback_note
        )
        logger.info(
            "Posted fallback PR comment on %s PR #%d",
            pr_info["repo_full_name"],
            pr_info["pr_number"],
        )

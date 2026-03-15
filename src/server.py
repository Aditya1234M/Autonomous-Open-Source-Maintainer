"""FastAPI webhook server — receives GitHub PR events and orchestrates the review pipeline."""

import hashlib
import hmac
import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from src.config import settings
from src.pipeline import review_pull_request

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(title="Autonomous Open Source Maintainer")


async def _run_review_pipeline(pr_info: dict[str, str | int]) -> None:
    """Run PR review pipeline and keep webhook endpoint stable on failures."""
    try:
        await review_pull_request(pr_info)
        logger.info("Completed review pipeline for PR #%s", pr_info["pr_number"])
    except Exception:
        logger.exception("Review pipeline failed for PR #%s", pr_info["pr_number"])


def _verify_signature(payload: bytes, signature: str) -> bool:
    """Verify the GitHub webhook HMAC-SHA256 signature."""
    if not signature:
        return False

    provided = signature.strip()
    if "=" not in provided:
        logger.warning("Webhook signature missing algorithm prefix")
        return False

    algo, provided_digest = provided.split("=", 1)
    algo = algo.lower()
    if algo not in {"sha256", "sha1"}:
        logger.warning("Unsupported webhook signature algorithm: %s", algo)
        return False

    hash_fn = hashlib.sha256 if algo == "sha256" else hashlib.sha1
    expected = hmac.new(
        settings.github_webhook_secret.strip().encode(),
        payload,
        hash_fn,
    ).hexdigest()

    # Compare digest values only, normalized to lowercase for robustness.
    matched = hmac.compare_digest(expected, provided_digest.lower())
    if not matched:
        logger.warning(
            (
                "Webhook signature mismatch "
                "(algo=%s, payload_len=%s, secret_len=%s, "
                "expected_prefix=%s, provided_prefix=%s)"
            ),
            algo,
            len(payload),
            len(settings.github_webhook_secret.strip()),
            expected[:12],
            provided_digest.lower()[:12],
        )

    return matched


@app.post("/webhook")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_hub_signature: str | None = Header(default=None),
    x_github_event: str = Header(...),
):
    """Handle incoming GitHub webhook events."""
    payload = await request.body()
    signature = x_hub_signature_256 or x_hub_signature

    if not _verify_signature(payload, signature or ""):
        raise HTTPException(status_code=401, detail="Invalid signature")

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"event type: {x_github_event}"}

    data = await request.json()
    action = data.get("action")

    if action not in ("opened", "synchronize", "reopened"):
        return {"status": "ignored", "reason": f"PR action: {action}"}

    pr_info = {
        "repo_full_name": data["repository"]["full_name"],
        "pr_number": data["pull_request"]["number"],
        "pr_url": data["pull_request"]["html_url"],
        "clone_url": data["repository"]["clone_url"],
        "head_sha": data["pull_request"]["head"]["sha"],
        "base_branch": data["pull_request"]["base"]["ref"],
        "head_branch": data["pull_request"]["head"]["ref"],
    }

    logger.info("Received PR #%s on %s", pr_info["pr_number"], pr_info["repo_full_name"])

    # Queue long-running work so GitHub receives a fast successful webhook response.
    background_tasks.add_task(_run_review_pipeline, pr_info)

    return {"status": "accepted", "pr": pr_info["pr_number"], "action": action}


@app.get("/health")
async def health():
    return {"status": "ok"}

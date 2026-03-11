"""FastAPI webhook server — receives GitHub PR events and orchestrates the review pipeline."""

import hashlib
import hmac
import logging

from fastapi import FastAPI, Header, HTTPException, Request

from src.config import settings
from src.pipeline import review_pull_request

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(title="Autonomous Open Source Maintainer")


def _verify_signature(payload: bytes, signature: str) -> bool:
    """Verify the GitHub webhook HMAC-SHA256 signature."""
    expected = hmac.new(
        settings.github_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


@app.post("/webhook")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(...),
    x_github_event: str = Header(...),
):
    """Handle incoming GitHub webhook events."""
    payload = await request.body()

    if not _verify_signature(payload, x_hub_signature_256):
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

    # Run the full review pipeline (non-blocking in production; inline here for clarity)
    await review_pull_request(pr_info)

    return {"status": "processing", "pr": pr_info["pr_number"]}


@app.get("/health")
async def health():
    return {"status": "ok"}

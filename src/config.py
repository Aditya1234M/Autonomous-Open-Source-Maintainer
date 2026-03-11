"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # GitHub
    github_token: str
    github_webhook_secret: str

    # AWS / Bedrock (Nova 2 Pro)
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str = "us-east-1"

    # Nova Act
    nova_act_api_key: str

    # App
    workdir: str = "/tmp/maintainer-workspaces"
    log_level: str = "INFO"
    max_repo_size_mb: int = 500
    test_timeout_seconds: int = 300

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()

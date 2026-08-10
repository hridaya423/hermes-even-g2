import os
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HERMES_G2_", env_file=os.getenv("HERMES_G2_ENV_FILE", ".env"))

    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    database_path: Path = Path("hermes-g2.db")
    attachments_root: Path = Path("/var/lib/hermes-g2/attachments")
    attachment_max_bytes: int = 25 * 1024 * 1024
    hermes_origin: str = "http://127.0.0.1:8642"
    hermes_api_key: SecretStr
    external_base_path: str = "/hermes-g2"
    pairing_ttl_seconds: int = 90
    action_max_age_seconds: int = 300
    event_retention_days: int = 30
    event_retention_floor: int = 10_000
    plugin_secret: SecretStr | None = None
    whisper_binary: Path = Path("/opt/homebrew/bin/whisper-cli")
    whisper_model: Path = Path("/var/lib/hermes-g2/models/ggml-tiny.en-q5_1.bin")
    summary_helper: Path = Path("/usr/local/libexec/hermes-g2-summary")
    tailscale_cli: Path = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
    diagnostics_audio: bool = False

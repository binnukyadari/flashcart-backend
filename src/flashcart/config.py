from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLASHCART_", extra="ignore", hide_input_in_errors=True
    )
    mode: Literal["memory", "gcp"] = "memory"
    api_key: SecretStr | None = None
    project_id: str | None = None
    database_url: SecretStr | None = None
    firestore_database: str = "(default)"
    pubsub_topic: str | None = None
    invoice_bucket: str | None = None

    @model_validator(mode="after")
    def cloud_config(self):
        if self.api_key is not None and len(self.api_key.get_secret_value()) < 32:
            raise ValueError("API key must be at least 32 characters")
        if self.mode == "gcp":
            if not all(
                (
                    self.api_key,
                    self.project_id,
                    self.database_url,
                    self.pubsub_topic,
                    self.invoice_bucket,
                )
            ):
                raise ValueError("gcp mode requires API key, project, database, topic and bucket")
            if not self.database_url.get_secret_value().strip():
                raise ValueError("Database connection string must not be empty")
            if not self.pubsub_topic.startswith(f"projects/{self.project_id}/topics/"):
                raise ValueError("Pub/Sub topic must be a full path in the configured project")
        return self

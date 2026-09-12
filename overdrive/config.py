from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_url: str = "http://localhost:8000"
    session_secret: str
    surreal_url: str = "http://localhost:8019"
    surreal_user: str = "overdrive_app"
    surreal_pass: str
    surreal_namespace: str = "overdrive"
    surreal_database: str = "local"
    local_login: bool = True
    google_login: bool = True
    google_client_id: str = ""
    google_client_secret: str = ""
    admin_email: str = ""
    admin_name: str = "Administrator"
    workspace_name: str = "Overdrive"
    dev_login_allow_remote: bool = False
    dev_login: bool = False
    dev_login_email: str = ""
    storage_path: Path = Path(".local/attachments")
    max_upload_bytes: int = 25 * 1024 * 1024

    @property
    def secure(self):
        return self.app_url.startswith("https://")

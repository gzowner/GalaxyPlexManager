from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Galaxy Plex Manager"
    app_env: str = "production"
    secret_key: str = "change-me"
    database_url: str = "sqlite:///./galaxyplex.db"
    admin_username: str = "admin"
    admin_email: str = "admin@example.com"
    admin_password: str = "change-me-now"
    session_https_only: bool = False
    tz: str = "America/Chicago"
    plex_image: str = "plexinc/pms-docker:public"
    plex_uid: int = 1000
    plex_gid: int = 1000
    default_node_name: str = "Local Plex Node"
    default_node_host: str = "127.0.0.1"
    default_node_base_path: str = "/opt/galaxyplexmanager/data"
    default_template_path: str = "/opt/galaxyplexmanager/templates/master"
    default_media_mounts: str = "[]"
    default_port_start: int = 32401
    default_port_end: int = 32999

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

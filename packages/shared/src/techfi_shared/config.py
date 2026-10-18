from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TECHFI_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

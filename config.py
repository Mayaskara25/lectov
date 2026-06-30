import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    litellm_model: str = "gemini/gemini-2.0-flash"
    litellm_model_fallbacks: str = ""  # comma-separated writer/repair fallbacks
    planner_model: str = "openrouter/openai/gpt-oss-120b:free"
    planner_model_fallbacks: str = (
        "openrouter/nvidia/nemotron-3-super-120b-a12b:free,"
        "openrouter/google/gemini-2.0-flash-exp:free"
    )
    gemini_api_key: str = ""
    openrouter_api_key: str = ""

    def model_list(self) -> list[str]:
        """Writer/repair model list: primary + fallbacks."""
        extras = [m.strip() for m in self.litellm_model_fallbacks.split(",") if m.strip()]
        return [self.litellm_model] + extras

    def planner_model_list(self) -> list[str]:
        """Planner model list: primary + fallbacks."""
        extras = [m.strip() for m in self.planner_model_fallbacks.split(",") if m.strip()]
        return [self.planner_model] + extras
    redis_url: str = "redis://localhost:6379/0"
    upload_dir: str = "uploads"
    scene_dir: str = "generated/scenes"
    video_dir: str = "generated/videos"
    max_upload_bytes: int = 10 * 1024 * 1024  # 10 MB
    llm_timeout: int = 60
    max_retries: int = 3

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()

for d in (settings.upload_dir, settings.scene_dir, settings.video_dir):
    os.makedirs(d, exist_ok=True)

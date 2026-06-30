from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE, override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    whisper_model: str = "whisper-1"
    max_recording_duration_seconds: int = 7200
    temp_dir: str = "./tmp"
    results_dir: str = "./data/results"
    database_url: str = ""
    prep_max_matching_questions: int = 120
    prep_max_other_questions: int = 30
    prep_max_topics: int = 20
    google_drive_enabled: bool = False
    google_drive_credentials_path: str = "./credentials/google-service-account.json"
    google_drive_folder_id: str = ""
    team_username: str = ""
    team_password: str = ""


settings = Settings()

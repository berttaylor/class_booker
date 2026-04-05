import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel, model_validator
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class AppConfig(BaseModel):
    base_url: str
    timezone: str
    login_endpoint: str
    availability_endpoint: str
    booking_endpoint: str
    list_bookings_endpoint: str
    cancel_booking_endpoint: str
    server_time_endpoint: str
    tutors_list_endpoint: str

class Settings(BaseSettings):
    config_path: Path = Path(__file__).parent.parent / "config.yaml"

    # Derived from the project directory name — no config needed
    service_name: str = Path.cwd().name

    # Secrets from .env
    teacher_sync_login_email: str | None = None
    teacher_sync_login_password: str | None = None

    # Set to false on secondary clones to skip the daily teacher sync (primary handles it).
    # When false, teachers_cache_path must be set to an absolute path pointing at the
    # primary clone's teachers.json.
    populate_teachers_enabled: bool = True
    teachers_cache_path: str = "teachers.json"

    @model_validator(mode="after")
    def check_secondary_cache_path(self) -> "Settings":
        if not self.populate_teachers_enabled and self.teachers_cache_path == "teachers.json":
            raise ValueError(
                "POPULATE_TEACHERS=false requires TEACHERS_CACHE_PATH to be set to an "
                "absolute path pointing at the primary clone's teachers.json"
            )
        return self

    # Pushover notifications (optional)
    pushover_user_key: str | None = None
    pushover_api_token: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

def load_app_config(path: Path) -> AppConfig:
    with open(path, "r") as f:
        config_data = yaml.safe_load(f)
    return AppConfig(**config_data)

settings = Settings()
app_config = load_app_config(settings.config_path)

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel, model_validator
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent

# Absolute paths throughout: cwd-relative resolution silently yields empty
# credentials and an empty teacher cache when the process starts anywhere other
# than the project root, with no error raised.
load_dotenv(BASE_DIR / ".env")

DEFAULT_TEACHERS_CACHE = "data/teachers.json"


class AppConfig(BaseModel):
    base_url: str
    timezone: str
    login_endpoint: str
    calendar_endpoint: str
    hold_endpoint: str
    confirm_endpoint: str
    my_classes_endpoint: str
    cancel_booking_endpoint: str
    tutors_list_endpoint: str
    activities_endpoint: str
    quota_endpoint: str


class Settings(BaseSettings):
    config_path: Path = BASE_DIR / "config.yaml"

    # Fixed, not derived from cwd — in a container that would become "app".
    service_name: str = "class_booker"

    # Secrets from .env
    teacher_sync_login_email: str | None = None
    teacher_sync_login_password: str | None = None

    # Set to false on secondary clones to skip the daily teacher sync (primary handles it).
    # When false, teachers_cache_path must be set to an absolute path pointing at the
    # primary clone's data/teachers.json.
    populate_teachers_enabled: bool = True
    teachers_cache_path: str = DEFAULT_TEACHERS_CACHE

    @model_validator(mode="after")
    def check_secondary_cache_path(self) -> "Settings":
        if (
            not self.populate_teachers_enabled
            and self.teachers_cache_path == DEFAULT_TEACHERS_CACHE
        ):
            raise ValueError(
                "POPULATE_TEACHERS=false requires TEACHERS_CACHE_PATH to be set to an "
                "absolute path pointing at the primary clone's data/teachers.json"
            )
        return self

    @property
    def teachers_cache_file(self) -> Path:
        """
        Absolute teacher cache path.

        The raw setting stays relative so the validator above can spot the
        untouched default; secondary clones override it with an absolute path.
        """
        return BASE_DIR / self.teachers_cache_path

    # Pushover notifications (optional)
    pushover_user_key: str | None = None
    pushover_api_token: str | None = None

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")


def load_app_config(path: Path) -> AppConfig:
    with open(path, "r") as f:
        config_data = yaml.safe_load(f)
    return AppConfig(**config_data)


settings = Settings()
app_config = load_app_config(settings.config_path)

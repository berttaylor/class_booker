import yaml
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel
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
    config_path: Path = Path("config.yaml")
    
    # Secrets from .env
    login_email: str | None = None
    login_password: str | None = None
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

def load_app_config(path: Path) -> AppConfig:
    with open(path, "r") as f:
        config_data = yaml.safe_load(f)
    return AppConfig(**config_data)

settings = Settings()
app_config = load_app_config(settings.config_path)

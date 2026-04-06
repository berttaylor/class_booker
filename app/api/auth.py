import json
import time
import base64
from pathlib import Path
from typing import Optional

from app.client import BookingClient
from app.config import app_config

TOKEN_CACHE_FILE = Path(__file__).parent.parent.parent / "cache" / ".teacher_sync_token_cache.json"


def is_token_expired(token: str, buffer_seconds: int = 30) -> bool:
    """
    Decodes the JWT payload to check if it's expired.
    JWT is Header.Payload.Signature
    """
    try:
        _, payload_b64, _ = token.split('.')
        missing_padding = len(payload_b64) % 4
        if missing_padding:
            payload_b64 += '=' * (4 - missing_padding)

        payload_json = base64.b64decode(payload_b64).decode('utf-8')
        payload = json.loads(payload_json)

        exp = payload.get("exp")
        if not exp:
            return False  # No exp claim, assume valid for now

        return time.time() > (exp - buffer_seconds)
    except Exception:
        return True  # If decoding fails, treat as expired


def get_cached_token(cache_file: Path) -> Optional[str]:
    if not cache_file.exists():
        return None
    try:
        with open(cache_file, "r") as f:
            data = json.load(f)
            token = data.get("access_token")
            if token and not is_token_expired(token):
                return token
    except Exception:
        pass
    return None


def _save_cached_token(token: str, cache_file: Path):
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump({"access_token": token}, f)
    except Exception as e:
        print(f"Warning: Failed to cache token: {e}")


def login(
    client: BookingClient,
    credentials: dict,
    cache_file: Path,
    use_cache: bool = True,
) -> Optional[str]:
    """
    Authenticates against the booking backend.
    Returns access_token if successful, None otherwise.
    credentials: dict with 'email' and 'password' keys.
    cache_file: path to the token cache file for this account.
    If use_cache=True, attempts to load a valid token from cache_file first.
    """
    if use_cache:
        cached_token = get_cached_token(cache_file)
        if cached_token:
            return cached_token

    data = {
        "email": credentials["email"],
        "password": credentials["password"],
    }

    response = client.post(app_config.login_endpoint, json=data)

    if response.status_code == 200:
        res_data = response.json()
        if res_data.get("status") == "success":
            token = res_data.get("access_token")
            if token:
                _save_cached_token(token, cache_file)
            return token

    return None

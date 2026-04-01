from datetime import datetime as dt, timezone
from typing import Dict, Any
from app.client import BookingClient
from app.config import app_config

def get_server_time(client: BookingClient) -> Dict[str, Any]:
    """
    Fetches the server time from the backend.
    """
    response = client.get(app_config.server_time_endpoint)
    
    if response.status_code != 200:
        return {"status": "error", "message": f"HTTP Error {response.status_code}: {response.text}"}
        
    try:
        # Based on the provided curl, we expect a JSON response.
        # Let's assume it returns a dict with the time or status.
        return response.json()
    except Exception as e:
        # If it's not JSON, maybe it's a raw string or we failed to parse
        return {"status": "error", "message": f"Failed to parse server time: {e}", "raw": response.text}

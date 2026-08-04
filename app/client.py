import httpx


class BookingClient:
    def __init__(self, base_url: str, timezone: str = "Europe/Madrid"):
        self.client = httpx.Client(
            base_url=base_url,
            timeout=30.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
                "Origin": "https://preview.worldsacross.com",
                "Referer": "https://preview.worldsacross.com/",
                "Accept": "application/json",
                "Accept-Language": "en",
                "Content-Type": "application/json",
                "x-timezone": timezone,
            },
        )

    def set_token(self, token: str):
        self.client.headers["Authorization"] = f"Bearer {token}"

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.client.post(url, **kwargs)

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.client.get(url, **kwargs)

    def close(self):
        self.client.close()

import httpx


class BookingClient:
    def __init__(self, base_url: str):
        self.client = httpx.Client(
            base_url=base_url,
            timeout=30.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                "Referer": "https://app.worldsacross.com/",
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
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

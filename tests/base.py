import respx
from app.client import BookingClient

TEST_BASE_URL = "http://localhost:9999"


class BaseTest:
    def setup_method(self, method):
        self._respx_mock = respx.mock(base_url=TEST_BASE_URL, assert_all_called=False)
        self.router = self._respx_mock.__enter__()
        self.mock_client = BookingClient(base_url=TEST_BASE_URL)

    def teardown_method(self, method):
        self.mock_client.close()
        self._respx_mock.__exit__(None, None, None)

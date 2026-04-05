import json
import respx
import httpx
from unittest.mock import patch


from app.notion import sync_teachers_to_notion, _has_changes, _extract_page_state, log_run_to_notion

NOTION_BASE = "https://api.notion.com/v1"

FAKE_CACHE = {
    "updated": "2026-04-05",
    "teachers": {
        "Maria Garcia": {"id": 184, "status": "ACTIVE"},
        "Carlos Lopez": {"id": 159, "status": "REMOVED"},
    },
}


def mock_settings(token="secret_token", db_id="db-id", run_log_db_id="log-db-id"):
    class FakeSettings:
        notion_api_token = token
        notion_teachers_database_id = db_id
        notion_run_log_database_id = run_log_db_id
        service_name = "test_service"
    return patch("app.notion.settings", FakeSettings())


def notion_query_response(existing_pages: list[dict]) -> httpx.Response:
    """Build a mock Notion database query response."""
    results = []
    for p in existing_pages:
        results.append({
            "id": p["id"],
            "properties": {
                "Name": {"title": [{"plain_text": p["name"]}]},
                "Platform ID": {"number": p.get("platform_id")},
                "Status": {"select": {"name": p.get("status", "ACTIVE")}},
                "Updated": {"date": {"start": p.get("updated", "2026-04-05")}},
            },
        })
    return httpx.Response(200, json={"results": results, "has_more": False})


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

class TestHasChanges:
    def _page_state(self, platform_id=184, status="ACTIVE", updated="2026-04-05"):
        return {"platform_id": platform_id, "status": status, "updated": updated}

    def _entry(self, id=184, status="ACTIVE"):
        return {"id": id, "status": status}

    def test_no_changes(self):
        assert not _has_changes(self._page_state(), self._entry(), "2026-04-05")

    def test_changed_status(self):
        assert _has_changes(self._page_state(status="ACTIVE"), self._entry(status="REMOVED"), "2026-04-05")

    def test_changed_platform_id(self):
        assert _has_changes(self._page_state(platform_id=100), self._entry(id=184), "2026-04-05")

    def test_unchanged_platform_id(self):
        assert not _has_changes(self._page_state(platform_id=184), self._entry(id=184), "2026-04-05")

    def test_changed_updated_date(self):
        assert _has_changes(self._page_state(updated="2026-01-01"), self._entry(), "2026-04-05")


class TestExtractPageState:
    def test_extracts_all_fields(self):
        page = {
            "properties": {
                "Platform ID": {"number": 184},
                "Status": {"select": {"name": "ACTIVE"}},
                "Updated": {"date": {"start": "2026-04-05"}},
            }
        }
        state = _extract_page_state(page)
        assert state == {"platform_id": 184, "status": "ACTIVE", "updated": "2026-04-05"}

    def test_handles_null_platform_id(self):
        page = {"properties": {"Platform ID": {"number": None},
                                "Status": {"select": None}, "Updated": {"date": None}}}
        state = _extract_page_state(page)
        assert state["platform_id"] is None

    def test_handles_null_status(self):
        page = {"properties": {"Status": {"select": None},
                                "Platform ID": {"number": None}, "Updated": {"date": None}}}
        state = _extract_page_state(page)
        assert state["status"] is None

    def test_handles_null_updated(self):
        page = {"properties": {"Updated": {"date": None},
                                "Platform ID": {"number": None}, "Status": {"select": None}}}
        state = _extract_page_state(page)
        assert state["updated"] is None


# ---------------------------------------------------------------------------
# sync_teachers_to_notion
# ---------------------------------------------------------------------------

@patch("app.teachers.save_teacher_cache")
class TestSyncTeachersToNotion:
    def test_skips_if_no_token(self, _save):
        with mock_settings(token=None):
            assert sync_teachers_to_notion(FAKE_CACHE) is False

    def test_skips_if_no_database_id(self, _save):
        with mock_settings(db_id=None):
            assert sync_teachers_to_notion(FAKE_CACHE) is False

    def test_creates_new_page_for_new_teacher(self, _save):
        with mock_settings():
            with respx.mock(assert_all_called=False) as router:
                router.post(f"{NOTION_BASE}/databases/db-id/query").mock(
                    return_value=notion_query_response([])
                )
                create_route = router.post(f"{NOTION_BASE}/pages").mock(
                    return_value=httpx.Response(200, json={"id": "new-page-id"})
                )
                result = sync_teachers_to_notion({
                    "updated": "2026-04-05",
                    "teachers": {"Maria Garcia": {"id": 184, "status": "ACTIVE"}},
                })
        assert result is True
        assert create_route.called

    def test_updates_changed_page(self, _save):
        with mock_settings():
            with respx.mock(assert_all_called=False) as router:
                # Existing page has REMOVED status, but cache has ACTIVE — should update
                router.post(f"{NOTION_BASE}/databases/db-id/query").mock(
                    return_value=notion_query_response([{
                        "id": "page-id", "name": "Maria Garcia",
                        "platform_id": 184, "status": "REMOVED", "updated": "2026-04-05",
                    }])
                )
                update_route = router.patch(f"{NOTION_BASE}/pages/page-id").mock(
                    return_value=httpx.Response(200, json={"id": "page-id"})
                )
                result = sync_teachers_to_notion({
                    "updated": "2026-04-05",
                    "teachers": {"Maria Garcia": {"id": 184, "status": "ACTIVE"}},
                })
        assert result is True
        assert update_route.called

    def test_skips_unchanged_page(self, _save):
        with mock_settings():
            with respx.mock(assert_all_called=False) as router:
                # Existing page matches cache exactly — should not update
                router.post(f"{NOTION_BASE}/databases/db-id/query").mock(
                    return_value=notion_query_response([{
                        "id": "page-id", "name": "Maria Garcia",
                        "platform_id": 184, "status": "ACTIVE", "updated": "2026-04-05",
                    }])
                )
                update_route = router.patch(f"{NOTION_BASE}/pages/page-id").mock(
                    return_value=httpx.Response(200, json={"id": "page-id"})
                )
                sync_teachers_to_notion({
                    "updated": "2026-04-05",
                    "teachers": {"Maria Garcia": {"id": 184, "status": "ACTIVE"}},
                })
        assert not update_route.called

    def test_writes_removed_status(self, _save):
        with mock_settings():
            with respx.mock(assert_all_called=False) as router:
                router.post(f"{NOTION_BASE}/databases/db-id/query").mock(
                    return_value=notion_query_response([{
                        "id": "page-id", "name": "Carlos Lopez",
                        "platform_id": 159, "status": "ACTIVE", "updated": "2026-01-01",
                    }])
                )
                update_route = router.patch(f"{NOTION_BASE}/pages/page-id").mock(
                    return_value=httpx.Response(200, json={"id": "page-id"})
                )
                sync_teachers_to_notion({
                    "updated": "2026-04-05",
                    "teachers": {"Carlos Lopez": {"id": 159, "status": "REMOVED"}},
                })
        body = json.loads(update_route.calls[0].request.content)
        assert body["properties"]["Status"]["select"]["name"] == "REMOVED"

    def test_returns_false_on_http_error(self, _save):
        with mock_settings():
            with respx.mock(assert_all_called=False) as router:
                router.post(f"{NOTION_BASE}/databases/db-id/query").mock(
                    side_effect=httpx.ConnectError("connection failed")
                )
                assert sync_teachers_to_notion(FAKE_CACHE) is False


# ---------------------------------------------------------------------------
# log_run_to_notion
# ---------------------------------------------------------------------------

class TestLogRunToNotion:
    def test_skips_if_no_database_id(self):
        with mock_settings(run_log_db_id=None):
            with respx.mock(assert_all_called=False) as router:
                create_route = router.post(f"{NOTION_BASE}/pages").mock(
                    return_value=httpx.Response(200, json={"id": "new-id"})
                )
                log_run_to_notion("Booked", "some detail")
        assert not create_route.called

    def test_skips_if_no_token(self):
        with mock_settings(token=None):
            with respx.mock(assert_all_called=False) as router:
                create_route = router.post(f"{NOTION_BASE}/pages").mock(
                    return_value=httpx.Response(200, json={"id": "new-id"})
                )
                log_run_to_notion("Booked", "some detail")
        assert not create_route.called

    def test_creates_page_with_correct_properties(self):
        with mock_settings():
            with respx.mock(assert_all_called=False) as router:
                create_route = router.post(f"{NOTION_BASE}/pages").mock(
                    return_value=httpx.Response(200, json={"id": "new-id"})
                )
                log_run_to_notion("Booked", "Maria Garcia — 2026-04-05 13:00", rule="mon_Monday Midday", teacher="Maria Garcia")
        assert create_route.called
        body = json.loads(create_route.calls[0].request.content)
        props = body["properties"]
        assert props["Status"]["select"]["name"] == "Booked"
        assert props["Detail"]["rich_text"][0]["text"]["content"] == "Maria Garcia — 2026-04-05 13:00"
        assert props["Rule"]["rich_text"][0]["text"]["content"] == "mon_Monday Midday"
        assert props["Teacher"]["rich_text"][0]["text"]["content"] == "Maria Garcia"
        assert body["parent"]["database_id"] == "log-db-id"

    def test_includes_job_field_when_provided(self):
        with mock_settings():
            with respx.mock(assert_all_called=False) as router:
                create_route = router.post(f"{NOTION_BASE}/pages").mock(
                    return_value=httpx.Response(200, json={"id": "new-id"})
                )
                log_run_to_notion("Booked", "detail", job="RUN_DUE")
        body = json.loads(create_route.calls[0].request.content)
        assert body["properties"]["Job"]["select"]["name"] == "RUN_DUE"

    def test_omits_job_field_when_not_provided(self):
        with mock_settings():
            with respx.mock(assert_all_called=False) as router:
                create_route = router.post(f"{NOTION_BASE}/pages").mock(
                    return_value=httpx.Response(200, json={"id": "new-id"})
                )
                log_run_to_notion("Booked", "detail")
        body = json.loads(create_route.calls[0].request.content)
        assert "Job" not in body["properties"]

    def test_silent_on_http_error(self):
        with mock_settings():
            with respx.mock(assert_all_called=False) as router:
                router.post(f"{NOTION_BASE}/pages").mock(
                    side_effect=httpx.ConnectError("connection failed")
                )
                log_run_to_notion("Error", "Auth failed")  # must not raise

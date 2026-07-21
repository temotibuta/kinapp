from pathlib import Path

from test_auth import register_and_login, setup_client


def test_active_frontend_does_not_use_html_injection_sinks():
    html = (Path(__file__).parents[1] / "static" / "index.html").read_text(encoding="utf-8")
    assert "innerHTML" not in html
    assert "outerHTML" not in html
    assert "insertAdjacentHTML" not in html
    assert "dangerouslySetInnerHTML" not in html


def test_rejects_negative_weight(tmp_path, monkeypatch):
    client, _ = setup_client(tmp_path, monkeypatch)
    headers = register_and_login(client, "alice")
    response = client.post(
        "/weights",
        headers=headers,
        json={"user_id": "alice", "date": "2026-07-21", "weight": -1},
    )
    assert response.status_code == 422


def test_rejects_oversized_food_name(tmp_path, monkeypatch):
    client, _ = setup_client(tmp_path, monkeypatch)
    headers = register_and_login(client, "alice")
    response = client.post(
        "/meals",
        headers=headers,
        json={
            "user_id": "alice", "date": "2026-07-21", "meal_type": "Lunch",
            "food_name": "x" * 201, "calories": 300, "protein": 5,
            "fat": 1, "carbs": 65, "salt": 0, "fiber": 1,
        },
    )
    assert response.status_code == 422


def test_rejects_invalid_visibility_and_extra_fields(tmp_path, monkeypatch):
    client, _ = setup_client(tmp_path, monkeypatch)
    headers = register_and_login(client, "alice")
    invalid = client.put(
        "/settings/visibility?current_user=alice",
        headers=headers,
        json={"visibility": "everyone"},
    )
    assert invalid.status_code == 422
    extra = client.post(
        "/weights",
        headers=headers,
        json={"user_id": "alice", "date": "2026-07-21", "weight": 70, "admin": True},
    )
    assert extra.status_code == 422


def test_security_headers_are_present(tmp_path, monkeypatch):
    client, _ = setup_client(tmp_path, monkeypatch)
    response = client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"

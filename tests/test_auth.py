import hashlib
import os
import sqlite3
import tempfile

from fastapi.testclient import TestClient

os.environ["KINAPP_DB_FILE"] = os.path.join(tempfile.gettempdir(), "kinapp-import-test.db")
import main


def setup_client(tmp_path, monkeypatch):
    database = tmp_path / "test.db"
    monkeypatch.setattr(main, "DB_FILE", str(database))
    main.init_db()
    return TestClient(main.app), database


def register_and_login(client, username):
    password = "safe-password-123"
    response = client.post("/register", json={"username": username, "password": password})
    assert response.status_code == 200
    response = client.post("/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_private_api_requires_login(tmp_path, monkeypatch):
    client, _ = setup_client(tmp_path, monkeypatch)
    assert client.get("/users/me?current_user=alice").status_code == 401


def test_user_cannot_claim_another_identity(tmp_path, monkeypatch):
    client, _ = setup_client(tmp_path, monkeypatch)
    headers = register_and_login(client, "alice")
    response = client.get("/users/me?current_user=bob", headers=headers)
    assert response.status_code == 403


def test_user_cannot_write_another_users_data(tmp_path, monkeypatch):
    client, _ = setup_client(tmp_path, monkeypatch)
    headers = register_and_login(client, "alice")
    response = client.post(
        "/weights",
        headers=headers,
        json={"user_id": "bob", "date": "2026-07-21", "weight": 70},
    )
    assert response.status_code == 403


def test_user_cannot_delete_another_users_meal(tmp_path, monkeypatch):
    client, database = setup_client(tmp_path, monkeypatch)
    alice_headers = register_and_login(client, "alice")
    bob_headers = register_and_login(client, "bob")
    response = client.post(
        "/meals",
        headers=bob_headers,
        json={
            "user_id": "bob", "date": "2026-07-21", "meal_type": "Lunch",
            "food_name": "Rice", "calories": 300, "protein": 5,
            "fat": 1, "carbs": 65, "salt": 0, "fiber": 1,
        },
    )
    assert response.status_code == 200
    with sqlite3.connect(database) as conn:
        meal_id = conn.execute("SELECT id FROM meals WHERE user_id = 'bob'").fetchone()[0]
    assert client.delete(f"/meals/{meal_id}", headers=alice_headers).status_code == 403
    assert client.delete(f"/meals/{meal_id}", headers=bob_headers).status_code == 200


def test_legacy_password_is_upgraded_after_login(tmp_path, monkeypatch):
    client, database = setup_client(tmp_path, monkeypatch)
    legacy = hashlib.sha256(b"old-password").hexdigest()
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO users (username, password) VALUES (?, ?)", ("legacy", legacy))
    response = client.post("/login", json={"username": "legacy", "password": "old-password"})
    assert response.status_code == 200
    with sqlite3.connect(database) as conn:
        upgraded = conn.execute("SELECT password FROM users WHERE username = 'legacy'").fetchone()[0]
    assert upgraded.startswith("pbkdf2_sha256$")
    assert upgraded != legacy


def test_user_cannot_add_exercise_to_another_users_session(tmp_path, monkeypatch):
    client, database = setup_client(tmp_path, monkeypatch)
    alice_headers = register_and_login(client, "alice")
    bob_headers = register_and_login(client, "bob")
    response = client.post(
        "/api/workout/sessions",
        headers=bob_headers,
        json={"user_id": "bob", "date": "2026-07-21", "duration": 60},
    )
    assert response.status_code == 200
    with sqlite3.connect(database) as conn:
        session_id = conn.execute("SELECT id FROM workout_sessions WHERE user_id = 'bob'").fetchone()[0]
    response = client.post(
        "/api/workout/exercises",
        headers=alice_headers,
        json={"session_id": session_id, "exercise_name": "Bench Press"},
    )
    assert response.status_code == 403

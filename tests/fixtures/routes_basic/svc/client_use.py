"""Test-client calls are requests, not routes."""


def test_reads(client):
    assert client.get("/api/v1/items/1").status_code == 200
    client.post("/api/v1/items", json={})

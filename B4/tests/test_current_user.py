"""`GET /api/v1/me` (`CHG-0032`): the caller's own account facts.

Exists only because the assignment mail body needs the student's display name/number and the
browser has no other legitimate source. The tests pin the two boundaries: authentication is
required, and the endpoint cannot be pointed at somebody else's account.
"""

from __future__ import annotations


def _student(factory, username: str, number: str) -> None:
    factory.user(
        username=username,
        password="correct horse battery staple",
        roles=("student",),
        display_name=f"Name {number}",
        student_number=number,
    )


def test_the_caller_reads_their_own_row(client, factory, sign_in_as):
    _student(factory, "me-one", "20249001")
    headers = sign_in_as("me-one").headers
    response = client.get("/api/v1/me", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["username"] == "me-one"
    assert body["display_name"] == "Name 20249001"
    assert body["student_number"] == "20249001"
    assert "student" in body["roles"]
    assert "email" not in response.text


def test_another_account_sees_its_own_details_not_someone_elses(client, factory, sign_in_as):
    _student(factory, "me-two", "20249002")
    _student(factory, "me-three", "20249003")
    response = client.get("/api/v1/me", headers=sign_in_as("me-two").headers)
    body = response.json()
    assert body["username"] == "me-two"
    assert body["student_number"] == "20249002"
    assert "20249003" not in response.text


def test_authentication_is_required(client):
    assert client.get("/api/v1/me").status_code == 401

"""RFC 9457 problem responses.

The envelope and status conventions come from `MAIN/contracts/openapi/common.yaml`; this
module only wires them into handlers. Two rules from the contract shape the design:

* `401` means "missing, malformed or expired credentials" and must not reveal whether the
  principal exists, so authentication failures share one detail string;
* `403` means authenticated-but-not-permitted and must never be downgraded to `404` to
  hide a resource, while `404` is only used once the caller is permitted to know.

Details never contain stack traces, SQL, internal paths or credentials.
"""

from __future__ import annotations

from dataclasses import dataclass

PROBLEM_BASE = "https://autograder.local/problems"


@dataclass(eq=False)
class ProblemError(Exception):
    """An error rendered as `application/problem+json` with the declared status."""

    status: int
    title: str
    detail: str
    slug: str

    def __post_init__(self) -> None:  # pragma: no cover - Exception init boilerplate
        super().__init__(self.detail)

    @property
    def type_uri(self) -> str:
        return f"{PROBLEM_BASE}/{self.slug}"

    def as_document(self, instance: str) -> dict[str, object]:
        return {
            "type": self.type_uri,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": instance,
        }


def bad_request(detail: str) -> ProblemError:
    return ProblemError(400, "Bad Request", detail, "bad-request")


def unauthenticated(detail: str | None = None) -> ProblemError:
    # One message for every authentication failure: unknown principal, wrong password,
    # expired session and revoked session are indistinguishable to the caller.
    return ProblemError(
        401,
        "Unauthorized",
        detail or "Invalid credentials or missing session.",
        "unauthorized",
    )


def forbidden(
    detail: str = "The role or course scope does not permit this operation.",
) -> ProblemError:
    return ProblemError(403, "Forbidden", detail, "forbidden")


def not_found(detail: str = "The requested resource does not exist.") -> ProblemError:
    return ProblemError(404, "Not Found", detail, "not-found")


def conflict(detail: str) -> ProblemError:
    return ProblemError(409, "Conflict", detail, "conflict")


def payload_too_large(detail: str) -> ProblemError:
    return ProblemError(413, "Payload Too Large", detail, "payload-too-large")


def unprocessable(detail: str) -> ProblemError:
    return ProblemError(422, "Unprocessable Entity", detail, "unprocessable-entity")


def too_many_requests(detail: str) -> ProblemError:
    return ProblemError(429, "Too Many Requests", detail, "too-many-requests")


def service_unavailable(dependency: str, detail: str) -> ProblemError:
    """A required dependency is unavailable; the response names the dependency."""
    return ProblemError(
        503,
        "Service Unavailable",
        f"{dependency} is unavailable: {detail}",
        "service-unavailable",
    )

"""B5's error surface: RFC 9457 Problem Details, nothing else.

The bodies match `MAIN/contracts/openapi/common.yaml` -> `ProblemDetails`
(`type`/`title`/`status`/`detail`/`instance`, `additionalProperties: false`). Status
semantics follow `MAIN/docs/api/governance.md` §2.2: `401` is "unauthenticated" and is
never used to mask anything else, and no `detail` string may carry a stack trace, a SQL
statement, an internal path or a credential.

B5 defines no `403`/`404`: it exposes one endpoint with no resources, so "authenticated but
not permitted" and "authorized but absent" have no meaning here. Adding them would be a
status the contract does not declare and the module cannot reach.
"""

from __future__ import annotations

from typing import Any

PROBLEM_TYPE_BASE = "https://autograder.local/problems/"
PROBLEM_MEDIA_TYPE = "application/problem+json"


class B5Error(Exception):
    """Base class for every B5 failure that has a defined response body."""

    status: int = 500
    title: str = "Internal Server Error"
    slug: str = "internal"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def to_problem(self, instance: str) -> dict[str, Any]:
        return {
            "type": f"{PROBLEM_TYPE_BASE}{self.slug}",
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": instance,
        }


class Unauthenticated(B5Error):
    """Missing, malformed, unverifiable or expired credentials.

    Also raised when the issuer's key set cannot be read: B5 cannot verify the token, so it
    refuses the request rather than serving a report to an unverified caller. The `detail`
    distinguishes the two cases for an operator without revealing whether a principal exists.
    """

    status = 401
    title = "Unauthorized"
    slug = "unauthorized"


class ContractTreeUnavailable(B5Error):
    """`MAIN/contracts/openapi/*.yaml` is absent or unreadable.

    This is a deployment fault, not a caller fault, and it is not reportable as a
    *difference*: the report's two `kind` values describe disagreements between the three
    views, and a missing view is not a disagreement. It fails loudly instead.
    """

    status = 500
    title = "Internal Server Error"
    slug = "contract-tree-unavailable"

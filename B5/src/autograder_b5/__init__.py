"""B5 -- the platform conformance auditor.

B5 exists to make the project's headline property demonstrable rather than asserted: a
module that obeys MAIN's specification can be plugged in without changing MAIN code
(PRD §8.6, ADR-0013). It is the first module written *after* the conventions froze, and it
is governed by the same gates as every other module.
"""

from .plugin import B5Module, create_module

__all__ = ["B5Module", "create_module"]

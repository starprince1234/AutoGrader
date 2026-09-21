"""DTO namespace - deliberately empty (ADR-0004).

The public/internals split must be visible on every import line, so this package
re-exports **nothing**:

    from autograder_b3.dto.public import QuestionPublic      # public surface
    from autograder_b3.dto.internal import TestCaseInternal  # protected surface

``from autograder_b3.dto import *`` yields no names on purpose. A test asserts
that this module exposes no public symbols and that ``dto.public`` never imports
``dto.internal``.
"""

from __future__ import annotations

__all__: tuple[str, ...] = ()

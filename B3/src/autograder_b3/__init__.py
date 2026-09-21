"""AutoGrader B3 - question bank, immutable question versions, evaluation packages
and de-identified statistics.

Boundaries this package enforces structurally (see ``docs/architecture/boundaries.md``):

* B3 never executes student code, never computes or stores a score, and never owns
  evaluation evidence.
* Public DTOs (``dto.public``) and protected DTOs (``dto.internal``) live in
  separate modules; responses leave through ``api.serializers.serialize_public``.
* Only the ``b3`` schema is accessed, with two least-privilege roles.
"""

from __future__ import annotations

from .plugin import B3Module, create_module

__all__ = ["B3Module", "create_module"]

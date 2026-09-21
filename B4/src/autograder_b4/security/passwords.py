"""Password hashing (Argon2id) and a uniform verification path.

Hard rule: no plaintext or reversible password is ever stored or logged. Argon2id is
the primary algorithm; bcrypt hashes are accepted on verification only so that a hash
imported from an earlier system can still be used, and are upgraded on next login
(`PHC` string prefix check). The algorithm name is persisted alongside the hash so a
future migration is data-driven rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exceptions
from argon2.low_level import Type

ALGORITHM_ARGON2ID = "argon2id"
ALGORITHM_BCRYPT = "bcrypt"
SUPPORTED_ALGORITHMS = (ALGORITHM_ARGON2ID, ALGORITHM_BCRYPT)

#: Tuned for the 0.15 vCPU PostgreSQL/host budget: ~19 MiB per hash, 2 iterations.
_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

#: Verified against when the principal does not exist, so that "unknown user" and
#: "wrong password" cost the same and neither is distinguishable by timing.
_DUMMY_HASH = _HASHER.hash("b4-dummy-password-never-matches")


@dataclass(frozen=True)
class HashedPassword:
    hash: str
    algorithm: str


def hash_password(password: str) -> HashedPassword:
    """Hash a password with Argon2id. The plaintext is not retained anywhere."""
    if not password:
        raise ValueError("password must not be empty")
    return HashedPassword(hash=_HASHER.hash(password), algorithm=ALGORITHM_ARGON2ID)


def verify_password(password: str, stored_hash: str, algorithm: str) -> bool:
    """Verify a password; returns False instead of raising on a malformed hash."""
    if algorithm == ALGORITHM_ARGON2ID:
        try:
            return _HASHER.verify(stored_hash, password)
        except (argon2_exceptions.VerifyMismatchError, argon2_exceptions.VerificationError):
            return False
        except argon2_exceptions.InvalidHashError:
            return False
    if algorithm == ALGORITHM_BCRYPT:  # pragma: no cover - no bcrypt rows are created by B4
        import bcrypt

        try:
            return bcrypt.checkpw(password.encode(), stored_hash.encode())
        except ValueError:
            return False
    raise ValueError(f"unsupported password hash algorithm: {algorithm!r}")


def needs_rehash(stored_hash: str, algorithm: str) -> bool:
    """True when the stored hash uses weaker parameters than the current policy."""
    if algorithm != ALGORITHM_ARGON2ID:
        return True
    try:
        return _HASHER.check_needs_rehash(stored_hash)
    except argon2_exceptions.InvalidHashError:
        return True


def burn_verification_time(password: str) -> None:
    """Spend the same work as a real verification for a principal that does not exist."""
    verify_password(password, _DUMMY_HASH, ALGORITHM_ARGON2ID)

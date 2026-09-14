from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PasswordHasher:
    cost: int = 2**14
    block_size: int = 8
    parallelism: int = 1
    salt_bytes: int = 16
    key_bytes: int = 32

    def hash(self, password: str) -> str:
        salt = secrets.token_bytes(self.salt_bytes)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=self.cost,
            r=self.block_size,
            p=self.parallelism,
            dklen=self.key_bytes,
        )
        encoded_salt = base64.urlsafe_b64encode(salt).decode("ascii")
        encoded_digest = base64.urlsafe_b64encode(digest).decode("ascii")
        return (
            f"scrypt$n={self.cost},r={self.block_size},p={self.parallelism}"
            f"${encoded_salt}${encoded_digest}"
        )

    def verify(self, password: str, encoded_hash: str) -> bool:
        try:
            algorithm, parameters, encoded_salt, encoded_digest = encoded_hash.split("$")
            if algorithm != "scrypt":
                return False
            parsed = dict(item.split("=", 1) for item in parameters.split(","))
            if (
                int(parsed["n"]) != self.cost
                or int(parsed["r"]) != self.block_size
                or int(parsed["p"]) != self.parallelism
            ):
                return False
            salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
            expected = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
            if len(salt) != self.salt_bytes or len(expected) != self.key_bytes:
                return False
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=int(parsed["n"]),
                r=int(parsed["r"]),
                p=int(parsed["p"]),
                dklen=len(expected),
            )
        except (KeyError, TypeError, ValueError):
            return False
        return hmac.compare_digest(actual, expected)

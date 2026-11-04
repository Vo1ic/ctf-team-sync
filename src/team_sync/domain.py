"""Shared domain errors and public record serialization."""

import hashlib
from datetime import datetime


class DomainError(Exception):
    def __init__(self, status: int, code: str, detail: str):
        self.status, self.code, self.detail = status, code, detail
        super().__init__(detail)


def fail(status, code, detail):
    raise DomainError(status, code, detail)


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def record(obj):
    return {
        column.name: (value.isoformat() if isinstance(value, datetime) else value)
        for column in obj.__table__.columns
        if column.name not in {"token_hash", "scope_hash", "fingerprint"}
        for value in [getattr(obj, column.name)]
    }

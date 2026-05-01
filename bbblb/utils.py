# Copyright (C) 2025, 2026  Marcel Hellkamp
# SPDX-License-Identifier: AGPL-3.0-or-later

import hashlib
import hmac
import typing
import re

# Common globals
SCOPE_SEPARATOR = "-bbblb-"
MAX_TENANT_NAME_LEN = 16
MAX_MEETING_ID_LEN = 256

# Common regular expressions
RE_MEETING_ID = re.compile("^[a-zA-Z0-9-_]{2,%d}$" % MAX_MEETING_ID_LEN)
RE_FORMAT_NAME = re.compile("^[a-zA-Z0-9]{1,64}$")
RE_RECORD_ID = re.compile("^[0-9a-f]{40}-\\d{12,}$")
RE_TENANT_NAME = re.compile("^[a-zA-Z0-9]{1,%d}$" % MAX_TENANT_NAME_LEN)


def add_scope(original_id: str, scope: str):
    return f"{original_id}{SCOPE_SEPARATOR}{scope}"


def split_scope(scoped_id: str) -> tuple[str, str | None]:
    """Split a scoped ID into the unscoped ID and the scope. If the
    input is not scoped, the returned scope will be None."""
    unscoped, sep, scope = scoped_id.rpartition(SCOPE_SEPARATOR)
    if not sep:
        return scoped_id, None
    return unscoped, scope


def remove_scope(scoped_id: str) -> str:
    """Returns the unscoped version of a scoped meeting id,
    or the original ID if there is no scope."""
    return split_scope(scoped_id)[0] or scoped_id


class cached_classproperty:
    def __init__(self, method):
        self.fget = method

    def __get__(self, instance, cls=None):
        result_field_name = self.fget.__name__ + "_property_result"

        if hasattr(cls, result_field_name):
            return getattr(cls, result_field_name)

        if not cls or not result_field_name:
            return self.fget(cls)

        setattr(cls, result_field_name, self.fget(cls))
        return getattr(cls, result_field_name)

    def getter(self, method):
        self.fget = method
        return self


T = typing.TypeVar("T")
R = typing.TypeVar("R")


def checked_cast(type_: type[T], value: typing.Any) -> T:
    if isinstance(value, type_):
        return value
    raise TypeError(f"Expected {type_} but got {type(value)}")


def hmac_sign(payload: str, secret: str, scope: str = "") -> str:
    """Sign payload with secret+scope, return a "signature:payload" string.

    The scope string acts as a salt for the secret so you can use the
    same secret for different contexts while preventing reuse of a known
    payload+signature pair in a different context.
    """
    sig = hmac.digest(
        (scope + secret).encode("UTF8"), payload.encode("UTF8"), hashlib.sha256
    )
    return f"{sig.hex()}:{payload}"


def hmac_verify(untrtusted: str, secret: str, scope: str = "") -> str | None:
    """Verify a "signature:payload" string against a secret+scope."""

    sig, sep, payload = untrtusted.partition(":")
    if sig and sep:
        check = hmac.digest(
            (scope + secret).encode("UTF8"), payload.encode("UTF8"), hashlib.sha256
        )
        try:
            if hmac.compare_digest(check, bytes.fromhex(sig)):
                return payload
        except ValueError:
            pass

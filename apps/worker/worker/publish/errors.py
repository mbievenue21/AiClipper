"""Exception types for the publish stage."""

from __future__ import annotations


class PublishError(RuntimeError):
    """Any failure during upload that we want surfaced in the UI."""


class AuthExpiredError(PublishError):
    """Access token rejected by the platform and we couldn't refresh it.

    Raising this should mark the upload as failed in a *non*-retryable way so
    the user is prompted to reconnect the account.
    """

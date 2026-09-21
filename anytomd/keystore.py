"""Secure API key storage via the OS credential store.

Uses `keyring` package which selects the platform-native backend:
- Windows → Windows Credential Manager (encrypted via DPAPI, per-user)
- macOS   → Keychain
- Linux   → Secret Service / KWallet

Keys are stored under service name "AnyToMD" with the provider name as the
username slot. Not encrypted with a user-provided password — encryption uses
the OS login session, so only the same OS user on the same machine can decrypt.

Threat model: this protects against casual disk theft, accidental git commit
(never touches project files), backup exposure, screen-share leaks. It does
NOT protect against malware running under the same user account, nor process
memory dumps while the app is active. That's the standard bar for desktop
apps — HSM/TPM would be needed to raise it.

If `keyring` isn't installed or no backend is available (e.g. headless Linux
without Secret Service), all functions return None / False gracefully so the
caller falls back to .env / env-var loading.
"""
from __future__ import annotations

from typing import Literal

_SERVICE_NAME = "AnyToMD"

KeySource = Literal["env", "keyring", "dotenv", "missing"]


def _kr():
    """Return the keyring module or None if unavailable."""
    try:
        import keyring  # type: ignore
        # Trigger backend init — some backends fail lazily
        keyring.get_keyring()
        return keyring
    except Exception:
        return None


def get_key(provider: str) -> str | None:
    """Return the stored key for `provider`, or None if not set / unavailable."""
    kr = _kr()
    if kr is None:
        return None
    try:
        val = kr.get_password(_SERVICE_NAME, provider)
    except Exception:
        return None
    return val or None


def set_key(provider: str, key: str) -> bool:
    """Store `key` for `provider`. Returns True on success."""
    kr = _kr()
    if kr is None:
        return False
    try:
        kr.set_password(_SERVICE_NAME, provider, key)
        return True
    except Exception:
        return False


def delete_key(provider: str) -> bool:
    """Remove the stored key for `provider`. Returns True even if nothing to
    delete (idempotent)."""
    kr = _kr()
    if kr is None:
        return False
    try:
        kr.delete_password(_SERVICE_NAME, provider)
    except Exception:
        # Missing entry raises PasswordDeleteError — treat as success (idempotent)
        pass
    return True


def is_available() -> bool:
    """True if a working keyring backend is present."""
    return _kr() is not None


def backend_name() -> str:
    """Return a short description of the active backend, or 'tidak tersedia'."""
    kr = _kr()
    if kr is None:
        return "tidak tersedia"
    try:
        return type(kr.get_keyring()).__name__
    except Exception:
        return "tidak diketahui"

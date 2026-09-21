import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional
from dotenv import load_dotenv

# Load .env from project root (parent of the anytomd/ package), so the app
# works regardless of current working directory (e.g. launched from a shortcut).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
# Also try CWD as a fallback — won't override values already set.
load_dotenv()

Provider = Literal["anthropic", "openai", "gemini", "moonshot", "ollama"]
ALL_PROVIDERS: tuple[Provider, ...] = ("anthropic", "openai", "gemini", "moonshot", "ollama")

ImageKind = Literal["decorative", "photo", "chart", "screenshot", "diagram", "unknown"]

# Source-of-truth marker for key display (used by GUI to show `🔑 keyring` vs `📄 .env`).
KeySource = Literal["env", "keyring", "dotenv", "missing"]

# Env var name per provider (both for reading and to know if the user
# explicitly set it in the shell vs. it came from .env).
_ENV_VAR: dict[Provider, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "gemini":    "GEMINI_API_KEY",
    "moonshot":  "MOONSHOT_API_KEY",
    "ollama":    "",  # no key
}

# Sprint 3 (C4): rough per-image cost estimate in USD, keyed by provider.
# "first" includes system-prompt cost; "cached" applies after the first call
# for providers that cache the system prompt (Anthropic). Tune here when
# switching models. Ollama = 0 (local).
_COST_TABLE: dict[str, dict[str, float]] = {
    "anthropic":  {"first": 0.013,  "cached": 0.010},
    "openai":     {"first": 0.006,  "cached": 0.006},   # gpt-4o vision
    "gemini":     {"first": 0.002,  "cached": 0.002},   # gemini-2.5-flash
    "moonshot":   {"first": 0.005,  "cached": 0.005},
    "ollama":     {"first": 0.0,    "cached": 0.0},
}

# Display labels for GUI dropdown
PROVIDER_LABELS: dict[Provider, str] = {
    "anthropic": "Anthropic (Claude)",
    "openai":    "OpenAI (ChatGPT)",
    "gemini":    "Google Gemini",
    "moonshot":  "Moonshot (Kimi)",
    "ollama":    "Ollama (offline / fallback)",
}


@dataclass
class Config:
    provider: Provider

    # Anthropic
    api_key: str                    # ANTHROPIC_API_KEY
    anthropic_model: str            # ANYTOMD_MODEL

    # Ollama
    ollama_host: str                # OLLAMA_HOST
    ollama_model: str               # ANYTOMD_OLLAMA_MODEL

    # OpenAI
    openai_api_key: str = ""        # OPENAI_API_KEY
    openai_model: str = "gpt-4o"    # ANYTOMD_OPENAI_MODEL
    openai_base_url: str = ""       # OPENAI_BASE_URL (Azure / proxy override)

    # Gemini
    gemini_api_key: str = ""        # GEMINI_API_KEY
    gemini_model: str = "gemini-2.5-flash"  # ANYTOMD_GEMINI_MODEL

    # Moonshot (Kimi) — OpenAI-compatible API
    moonshot_api_key: str = ""      # MOONSHOT_API_KEY
    moonshot_model: str = "moonshot-v1-32k-vision-preview"  # ANYTOMD_MOONSHOT_MODEL
    moonshot_base_url: str = "https://api.moonshot.cn/v1"   # MOONSHOT_BASE_URL

    # Sprint 3 (C1): image provider overrides.
    image_provider: Optional[Provider] = None
    image_provider_by_kind: dict[str, Provider] = field(default_factory=dict)

    # Sprint 3 (C4): budget cap. 0 = unlimited.
    cost_cap_usd: float = 0.0

    @classmethod
    def from_env(cls) -> "Config":
        def _prov(name: str, default: Provider = "anthropic") -> Provider:
            v = os.getenv(name, default).strip().lower()
            return v if v in ALL_PROVIDERS else default  # type: ignore[return-value]

        provider = _prov("ANYTOMD_PROVIDER", "anthropic")

        img_raw = os.getenv("ANYTOMD_IMAGE_PROVIDER", "").strip().lower()
        image_provider: Optional[Provider] = (
            img_raw if img_raw in ALL_PROVIDERS else None
        )  # type: ignore[assignment]

        per_kind: dict[str, Provider] = {}
        for kind in ("diagram", "chart", "photo", "screenshot", "decorative", "unknown"):
            v = os.getenv(f"ANYTOMD_IMAGE_PROVIDER_FOR_{kind.upper()}", "").strip().lower()
            if v in ALL_PROVIDERS:
                per_kind[kind] = v  # type: ignore[assignment]

        try:
            cap = float(os.getenv("ANYTOMD_COST_CAP_USD", "0").strip() or 0)
        except ValueError:
            cap = 0.0

        # Key loading precedence: env var (highest) → keyring → empty.
        # `.env` values already hydrated os.environ by python-dotenv at import.
        return cls(
            provider=provider,
            api_key=_load_key("anthropic"),
            anthropic_model=os.getenv("ANYTOMD_MODEL", "claude-sonnet-4-6").strip(),
            ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434").strip(),
            ollama_model=os.getenv("ANYTOMD_OLLAMA_MODEL", "qwen2.5vl:7b").strip(),
            openai_api_key=_load_key("openai"),
            openai_model=os.getenv("ANYTOMD_OPENAI_MODEL", "gpt-4o").strip(),
            openai_base_url=os.getenv("OPENAI_BASE_URL", "").strip(),
            gemini_api_key=_load_key("gemini"),
            gemini_model=os.getenv("ANYTOMD_GEMINI_MODEL", "gemini-2.5-flash").strip(),
            moonshot_api_key=_load_key("moonshot"),
            moonshot_model=os.getenv(
                "ANYTOMD_MOONSHOT_MODEL", "moonshot-v1-32k-vision-preview"
            ).strip(),
            moonshot_base_url=os.getenv(
                "MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1"
            ).strip(),
            image_provider=image_provider,
            image_provider_by_kind=per_kind,
            cost_cap_usd=cap,
        )

    def reload_keys(self) -> None:
        """Re-read all keys from env/keyring. Called by GUI after user saves
        or deletes a key so the running app picks it up without restart."""
        self.api_key = _load_key("anthropic")
        self.openai_api_key = _load_key("openai")
        self.gemini_api_key = _load_key("gemini")
        self.moonshot_api_key = _load_key("moonshot")

    def key_source(self, provider: Provider) -> KeySource:
        """Return where the key came from — for GUI display."""
        if provider == "ollama":
            return "keyring" if self.ollama_host else "missing"
        var = _ENV_VAR.get(provider, "")
        if var and os.getenv(var, "").strip():
            # Env var set — but distinguish "shell env" from ".env file".
            # python-dotenv sets os.environ from .env only if the key wasn't
            # already there. We can't reliably distinguish after loading, so
            # treat any env-var value as `env`. (User who wants explicit
            # "keyring only" should delete the .env line.)
            return "env"
        # Env empty — check keyring
        try:
            from . import keystore
            if keystore.get_key(provider):
                return "keyring"
        except Exception:
            pass
        return "missing"

    # ---------- Provider selection ----------

    def provider_for_kind(self, kind: str) -> Provider:
        """Return which provider should handle an image of `kind`."""
        if kind in self.image_provider_by_kind:
            return self.image_provider_by_kind[kind]
        if self.image_provider is not None:
            return self.image_provider
        return self.provider

    def all_providers_in_use(self) -> set[Provider]:
        out: set[Provider] = {self.provider}
        if self.image_provider:
            out.add(self.image_provider)
        out.update(self.image_provider_by_kind.values())
        return out

    def has_key_for(self, provider: Provider) -> bool:
        if provider == "anthropic":
            return bool(self.api_key)
        if provider == "openai":
            return bool(self.openai_api_key)
        if provider == "gemini":
            return bool(self.gemini_api_key)
        if provider == "moonshot":
            return bool(self.moonshot_api_key)
        if provider == "ollama":
            return bool(self.ollama_host and self.ollama_model)
        return False

    def model_for(self, provider: Provider) -> str:
        return {
            "anthropic": self.anthropic_model,
            "openai": self.openai_model,
            "gemini": self.gemini_model,
            "moonshot": self.moonshot_model,
            "ollama": self.ollama_model,
        }[provider]

    # ---------- Readiness ----------

    @property
    def is_ready(self) -> bool:
        for prov in self.all_providers_in_use():
            if not self.has_key_for(prov):
                return False
        return True

    @property
    def not_ready_reason(self) -> str:
        missing = [p for p in self.all_providers_in_use() if not self.has_key_for(p)]
        if not missing:
            return ""
        names = ", ".join(PROVIDER_LABELS.get(p, p) for p in missing)
        return f"API key / config belum lengkap untuk: {names}"

    @property
    def display_model(self) -> str:
        main = self.model_for(self.provider)
        extras = self.all_providers_in_use() - {self.provider}
        if extras:
            return f"{main} (+ hybrid: {', '.join(sorted(extras))})"
        return main


def _load_key(provider: Provider) -> str:
    """Load a key with precedence: env var → keyring → empty. Never raises."""
    var = _ENV_VAR.get(provider, "")
    if var:
        v = os.getenv(var, "").strip()
        if v:
            return v
    try:
        from . import keystore
        v = keystore.get_key(provider)
        if v:
            return v.strip()
    except Exception:
        pass
    return ""


def cost_per_call(provider: str, is_first: bool = False) -> float:
    tbl = _COST_TABLE.get(provider, {"first": 0.0, "cached": 0.0})
    return tbl["first"] if is_first else tbl["cached"]


# Backwards-compat: old code called `cost_per_anthropic_call`. Keep as thin alias.
def cost_per_anthropic_call(is_first: bool = False) -> float:
    return cost_per_call("anthropic", is_first)

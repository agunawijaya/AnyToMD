"""PDF page vision against a real cloud model (needs an API key).

Converts tests/fixtures/slide_deck.pdf with page vision forced on and checks
what only page vision recovers: the pipeline as Mermaid with the right edge
direction, the phase of each box from the legend colours, and the table that
has no rulings. Uses ANYTOMD_PROVIDER (default anthropic); skips if the
provider is Ollama or its key is missing.

    .venv\\Scripts\\python.exe tests\\run_page_vision_smoke.py
"""
from __future__ import annotations

import io
import os
import re
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
os.environ.setdefault("ANYTOMD_PROVIDER", "anthropic")

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
OUT = HERE / "output"
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(HERE.parent))

from anytomd.ai_client import _validate_mermaid, create_client  # noqa: E402
from anytomd.config import Config  # noqa: E402
from anytomd.converters import convert  # noqa: E402
from anytomd.options import ConvertOptions  # noqa: E402


def main() -> int:
    cfg = Config.from_env()
    if cfg.provider == "ollama" or not cfg.is_ready:
        print(f"SKIP: needs a cloud provider with a key ({cfg.provider}: {cfg.not_ready_reason or 'local'})")
        return 0
    print(f"Provider: {cfg.provider}   Model: {cfg.display_model}")
    opts = ConvertOptions(pdf_page_vision="always")
    t0 = time.time()
    md = convert(FIX / "slide_deck.pdf", create_client(cfg),
                 progress=lambda m: print(f"  · {m}"), options=opts)
    out = OUT / "slide_deck_pdf_page_vision.md"
    out.write_text(md, encoding="utf-8")
    print(f"  {time.time() - t0:.1f}s -> {out}")
    print(f"  stats: {opts.stats_summary()}")

    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        print(f"  {'ok  ' if cond else 'FAIL'} {name}")
        if not cond:
            failures.append(name)

    blocks = re.findall(r"```mermaid\n(.*?)```", md, re.S)
    check("pipeline rendered as Mermaid", len(blocks) >= 1)
    check("every Mermaid block valid", all(_validate_mermaid(b)[0] for b in blocks))
    flat = " ".join(md.split())
    check("edges follow the arrows (source → … → answer)",
          bool(blocks) and all(w in blocks[0] for w in ("Source system", "Extractor job",
                                                         "Search index", "Voice agent"))
          and "-->" in blocks[0])
    check("Voice agent carries its Phase 2 colour", bool(re.search(r"Voice agent[^\n]*Phase 2", md)))
    check("borderless table recovered", bool(re.search(r"\|\s*\**Real-time voice\**\s*\|\s*LiveKit server\s*\|", md)))
    check("template logo not described", "ACME" not in flat and "Figure" not in flat)
    check("footer stripped", "Confidential" not in flat)
    check("text coverage ≥ 95%", opts._stats["page_vision_min_coverage"] >= 0.95)
    print("ALL PASSED" if not failures else f"{len(failures)} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

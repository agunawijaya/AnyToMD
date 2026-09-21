"""Smoke test the token-saving options against dedup_test.pdf.

The fixture has the same 32x32 logo on all 6 pages + 1 unique diagram on page 6.
Without optimizations: 7 AI calls (6 logos + 1 diagram).
With dedup only:       2 AI calls (1 logo, cached for the other 5, + 1 diagram).
With min_dim=64:       1 AI call (logo skipped as tiny, only diagram sent).
With mode=none:        0 AI calls.
"""
import io
import os
import sys
import time
from pathlib import Path

os.environ["ANYTOMD_PROVIDER"] = "ollama"
os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")
os.environ.setdefault("ANYTOMD_OLLAMA_MODEL", "qwen2.5vl:7b")

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures" / "dedup_test.pdf"
OUT = HERE / "output"
OUT.mkdir(exist_ok=True)

sys.path.insert(0, str(HERE.parent))

from anytomd.ai_client import create_client
from anytomd.config import Config
from anytomd.converters import convert
from anytomd.options import ConvertOptions


def _run(label: str, options: ConvertOptions, ai) -> None:
    print(f"\n=== {label} ===", flush=True)
    print(f"    options: dedup={options.image_dedup} "
          f"min_dim={options.image_min_dim} "
          f"min_bytes={options.image_min_bytes} "
          f"mode={options.image_mode}", flush=True)
    t0 = time.time()
    md = convert(FIX, ai, progress=lambda m: print(f"    · {m}", flush=True), options=options)
    dur = time.time() - t0
    tag = "".join(c if c.isalnum() or c == "_" else "_" for c in label.lower().replace(" ", "_"))
    out = OUT / f"dedup_test__{tag}.md"
    out.write_text(md, encoding="utf-8")
    stats = options._stats
    total = sum(stats.values())
    print(f"    stats: {options.stats_summary()}", flush=True)
    print(f"    time: {dur:.1f}s   total_images_processed={total}   output: {out.name}", flush=True)


def main() -> int:
    cfg = Config.from_env()
    if not cfg.is_ready:
        print(f"NOT READY: {cfg.not_ready_reason}", file=sys.stderr)
        return 1
    ai = create_client(cfg)
    print(f"Provider: {cfg.provider}   Model: {cfg.display_model}", flush=True)

    _run("Baseline (no optimizations)",
         ConvertOptions(), ai)
    _run("Dedup ON",
         ConvertOptions(image_dedup=True), ai)
    _run("Skip < 64px",
         ConvertOptions(image_min_dim=64), ai)
    _run("Diagrams-only heuristic",
         ConvertOptions(image_mode="diagrams_only"), ai)
    _run("Full skip (mode=none)",
         ConvertOptions(image_mode="none"), ai)
    _run("Dedup + skip-tiny combined",
         ConvertOptions(image_dedup=True, image_min_dim=64), ai)
    return 0


if __name__ == "__main__":
    sys.exit(main())

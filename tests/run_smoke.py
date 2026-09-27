"""Force Ollama provider and run each converter. Writes outputs to tests/output/."""
import io
import os
import sys
import time
from pathlib import Path

# Force UTF-8 output so arrow chars etc. don't crash on Windows cp1252 console.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)

# Force Ollama BEFORE any anytomd import (config reads env at import time).
os.environ["ANYTOMD_PROVIDER"] = "ollama"
os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")
os.environ.setdefault("ANYTOMD_OLLAMA_MODEL", "qwen2.5vl:7b")

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
OUT = HERE / "output"
OUT.mkdir(exist_ok=True)

sys.path.insert(0, str(HERE.parent))

from anytomd.ai_client import create_client  # noqa: E402
from anytomd.config import Config  # noqa: E402
from anytomd.converters import convert  # noqa: E402


def _run(name: str, path: Path, ai) -> None:
    print(f"\n=== {name}: {path.name} ===", flush=True)
    t0 = time.time()
    try:
        md = convert(path, ai, progress=lambda m: print(f"  · {m}", flush=True))
    except Exception as e:
        print(f"  FAILED: {e}", flush=True)
        return
    dur = time.time() - t0
    out = OUT / f"{path.stem}_{path.suffix.lstrip('.')}.md"
    out.write_text(md, encoding="utf-8")
    print(f"  ok in {dur:.1f}s -> {out}  ({len(md)} chars)", flush=True)


def main() -> int:
    cfg = Config.from_env()
    print(f"Provider: {cfg.provider}   Model: {cfg.display_model}", flush=True)
    if not cfg.is_ready:
        print(f"NOT READY: {cfg.not_ready_reason}", file=sys.stderr)
        return 1
    ai = create_client(cfg)

    _run("IMAGE", FIX / "diagram.png", ai)
    _run("PDF", FIX / "sample.pdf", ai)
    _run("PDF (slides, text path)", FIX / "slide_deck.pdf", ai)
    _run("PPTX (picture)", FIX / "sample.pptx", ai)
    _run("PPTX (shapes)", FIX / "shape_diagram.pptx", ai)
    _run("DOCX", FIX / "sample.docx", ai)
    _run("XLSX", FIX / "sample.xlsx", ai)
    _run("HTML", FIX / "sample.html", ai)
    return 0


if __name__ == "__main__":
    sys.exit(main())

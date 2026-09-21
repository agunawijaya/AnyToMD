"""Smoke test OCR modes against scanned.pdf."""
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
FIX = HERE / "fixtures" / "scanned.pdf"
OUT = HERE / "output"
OUT.mkdir(exist_ok=True)

sys.path.insert(0, str(HERE.parent))

from anytomd.ai_client import create_client
from anytomd.config import Config
from anytomd.converters import convert
from anytomd.options import ConvertOptions


def _run(label: str, options: ConvertOptions, ai) -> None:
    print(f"\n=== {label} ===", flush=True)
    print(f"    options: ocr_mode={options.ocr_mode} engine={options.ocr_engine} lang={options.ocr_lang}", flush=True)
    t0 = time.time()
    md = convert(FIX, ai, progress=lambda m: print(f"    · {m}", flush=True), options=options)
    dur = time.time() - t0
    tag = "".join(c if c.isalnum() or c == "_" else "_" for c in label.lower().replace(" ", "_"))
    out = OUT / f"scanned__{tag}.md"
    out.write_text(md, encoding="utf-8")
    print(f"    time: {dur:.1f}s   output: {out.name}   ({len(md)} chars)", flush=True)
    if options.ocr_enabled:
        print(f"    stats: {options.stats_summary()}", flush=True)


def main() -> int:
    cfg = Config.from_env()
    ai = create_client(cfg)
    print(f"Provider: {cfg.provider}   Model: {cfg.display_model}", flush=True)

    _run("OCR OFF (baseline)",
         ConvertOptions(), ai)
    _run("OCR auto + Tesseract",
         ConvertOptions(ocr_mode="auto", ocr_engine="tesseract", ocr_lang="ind+eng"), ai)
    _run("OCR always + Tesseract",
         ConvertOptions(ocr_mode="always", ocr_engine="tesseract", ocr_lang="ind+eng"), ai)
    _run("OCR auto + Hybrid",
         ConvertOptions(ocr_mode="auto", ocr_engine="hybrid", ocr_lang="ind+eng"), ai)
    _run("OCR auto + AI",
         ConvertOptions(ocr_mode="auto", ocr_engine="ai", ocr_lang="ind+eng"), ai)
    return 0


if __name__ == "__main__":
    sys.exit(main())

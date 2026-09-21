"""Render the shape-diagram slide and save the raster as a preview PNG."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from pptx import Presentation
from anytomd.slide_render import render_slide_shapes

prs = Presentation(str(HERE / "fixtures" / "shape_diagram.pptx"))
slide = prs.slides[0]
png = render_slide_shapes(slide, prs.slide_width, prs.slide_height)
out = HERE / "output" / "shape_diagram_render.png"
out.parent.mkdir(exist_ok=True)
out.write_bytes(png)
print(f"Rendered: {out}  ({len(png)} bytes)")

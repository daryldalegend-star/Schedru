"""Turn packaging/icon-1024.png into the .icns the Mac app uses and the
smaller PNG the window uses. Run after build_icon.mjs."""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
source = Image.open(ROOT / "packaging" / "icon-1024.png").convert("RGBA")
assert source.size == (1024, 1024), source.size

source.save(
    ROOT / "packaging" / "Schedru.icns",
    format="ICNS",
    sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)],
)
source.resize((256, 256), Image.LANCZOS).save(ROOT / "syllabus_cal" / "app" / "web" / "icon.png")
print("wrote packaging/Schedru.icns and syllabus_cal/app/web/icon.png")

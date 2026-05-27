# convert_to_pil_ready.py
# Re-encode images into clean, PIL-friendly PNGs for OCR.
# - Preserves subfolder structure
# - Fixes orientation, strips EXIF, normalizes color mode
# - Supports JPG/JPEG/PNG/WebP/BMP/TIFF/JFIF and (optionally) HEIC/HEIF/AVIF
# - Writes a CSV report with status for every file

import os
import sys
import csv
from pathlib import Path
from datetime import datetime

from PIL import Image, ImageOps, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True  # be forgiving with slightly damaged files

# Optional: enable HEIC/HEIF if pillow-heif is installed
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HAVE_HEIF = True
except Exception:
    HAVE_HEIF = False

# Optional: enable AVIF if pillow-avif-plugin is installed
try:
    import pillow_avif  # noqa: F401  # just registering the plugin
    HAVE_AVIF = True
except Exception:
    HAVE_AVIF = False

# ====== USER CONFIG ======
INPUT_ROOT = os.getenv("ZOMATO_IMAGE_INPUT_ROOT", "zomato_menu_images")
OUTPUT_ROOT = str(Path(INPUT_ROOT).with_name(Path(INPUT_ROOT).name + "_PIL_ready"))
OUTPUT_FORMAT = "PNG"  # PNG is safest for OCR. Change to "JPEG" if you prefer.
JPEG_QUALITY = 92
# =========================

ALLOWED_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif", ".jfif", ".avif", ".heif", ".heic"
}

def pil_safe_save(img: Image.Image, out_path: Path):
    """Save image as a clean, OCR-friendly file (PNG by default)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = ImageOps.exif_transpose(img)        # auto-fix rotation
    if img.mode not in ("RGB", "L", "LA"):
        img = img.convert("RGB")              # normalize color mode

    params = {}
    if OUTPUT_FORMAT.upper() == "JPEG":
        params = dict(quality=JPEG_QUALITY, optimize=True, subsampling="4:2:0")
        # Ensure RGB for JPEG
        if img.mode != "RGB":
            img = img.convert("RGB")
    elif OUTPUT_FORMAT.upper() == "PNG":
        params = dict(optimize=True, compress_level=6)

    # strip EXIF/meta by re-saving (Pillow drops it unless explicitly given)
    img.save(out_path, format=OUTPUT_FORMAT, **params)

def is_image_file(p: Path) -> bool:
    return p.suffix.lower() in ALLOWED_EXTS

def main():
    src_root = Path(INPUT_ROOT)
    dst_root = Path(OUTPUT_ROOT)
    dst_root.mkdir(parents=True, exist_ok=True)

    report_path = dst_root / ("conversion_report_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
    total, ok, fail, skipped = 0, 0, 0, 0

    with report_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source_path", "dest_path", "status", "note"])

        for src_path in src_root.rglob("*"):
            if not src_path.is_file():
                continue
            if not is_image_file(src_path):
                continue

            total += 1
            rel = src_path.relative_to(src_root)

            # output filename keeps base name but uses .png or .jpg depending on OUTPUT_FORMAT
            out_ext = ".png" if OUTPUT_FORMAT.upper() == "PNG" else ".jpg"
            out_path = dst_root / rel.with_suffix(out_ext)

            # Skip if already converted
            if out_path.exists():
                w.writerow([str(src_path), str(out_path), "skipped", "already exists"])
                skipped += 1
                continue

            try:
                with Image.open(src_path) as im:
                    # Force load to catch issues early
                    im.load()
                    pil_safe_save(im, out_path)
                w.writerow([str(src_path), str(out_path), "ok", ""])
                ok += 1
            except Exception as e:
                # As a last resort, try to read bytes and re-open (sometimes helps)
                try:
                    raw = src_path.read_bytes()
                    with Image.open(Path(src_path)) as im:
                        im.load()
                        pil_safe_save(im, out_path)
                    w.writerow([str(src_path), str(out_path), "ok_after_retry", ""])
                    ok += 1
                except Exception as e2:
                    w.writerow([str(src_path), str(out_path), "fail", f"{type(e2).__name__}: {e2}"])
                    fail += 1

    print(f"\nDone.\nTotal candidate images: {total}\nConverted: {ok}\nSkipped: {skipped}\nFailed: {fail}")
    print(f"Report: {report_path}")
    print(f"Converted images root: {dst_root}")

if __name__ == "__main__":
    # Allow overriding the input folder from command line:
    #    python convert_to_pil_ready.py "C:\path\to\root"
    if len(sys.argv) > 1:
        INPUT = sys.argv[1]
        if INPUT:
            globals()["INPUT_ROOT"] = INPUT
            globals()["OUTPUT_ROOT"] = str(Path(INPUT).with_name(Path(INPUT).name + "_PIL_ready"))
    main()

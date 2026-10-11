"""Build official Qwen icon assets for Windows Desktop application and web UI."""
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
WEB = ROOT / "web"
ASSETS.mkdir(parents=True, exist_ok=True)
WEB.mkdir(parents=True, exist_ok=True)

# Clean up any scratch files
for scratch in [ASSETS / "test_extracted.png", ROOT / "tools" / "generate_icon.py"]:
    if scratch.exists():
        scratch.unlink(missing_ok=True)

# 1. Load the master logo and crop the actual Qwen emblem
original_logo = Image.open(ASSETS / "logo.png").convert("RGBA")
# Exact crop of the Qwen emblem
emblem = original_logo.crop((347, 284, 768, 701))
emblem_path = ASSETS / "qwen_icon.png"
emblem.save(emblem_path, format="PNG")
print(f"[1/5] Extracted actual Qwen emblem ({emblem.size}): {emblem_path}")

# 2. Build 512x512 high-res Desktop App Icon with premium squircle backdrop
canvas_size = 512
margin = 28
radius = 104
box = [margin, margin, canvas_size - margin, canvas_size - margin]

# Background squircle
bg = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
bg_draw = ImageDraw.Draw(bg)

# Deep space indigo gradient matching the Qwen violet aesthetic
for y in range(margin, canvas_size - margin):
    ratio = (y - margin) / (canvas_size - 2 * margin)
    cr = int(24 * (1 - ratio) + 12 * ratio)
    cg = int(22 * (1 - ratio) + 14 * ratio)
    cb = int(58 * (1 - ratio) + 32 * ratio)
    bg_draw.line([(margin, y), (canvas_size - margin, y)], fill=(cr, cg, cb, 255))

# Mask with rounded rectangle
mask = Image.new("L", (canvas_size, canvas_size), 0)
m_draw = ImageDraw.Draw(mask)
m_draw.rounded_rectangle(box, radius=radius, fill=255)

base = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
base.paste(bg, (0, 0), mask)

# Glowing squircle border
border = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
b_draw = ImageDraw.Draw(border)
b_draw.rounded_rectangle(box, radius=radius, outline=(129, 140, 248, 220), width=5)

# Inner soft highlight
inner_box = [margin + 4, margin + 4, canvas_size - margin - 4, canvas_size - margin - 4]
b_draw.rounded_rectangle(inner_box, radius=radius - 4, outline=(199, 210, 254, 70), width=2)

base = Image.alpha_composite(base, border)

# Ambient purple/violet bloom for the emblem
bloom_size = 360
emblem_resized = emblem.resize((bloom_size, int(bloom_size * (emblem.height / emblem.width))), Image.Resampling.LANCZOS)
bloom = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
bx = (canvas_size - emblem_resized.width) // 2
by = (canvas_size - emblem_resized.height) // 2
bloom.paste(emblem_resized, (bx, by), emblem_resized)
bloom_blurred = bloom.filter(ImageFilter.GaussianBlur(26))

# Compose final 512x512
final_512 = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
final_512 = Image.alpha_composite(final_512, base)
final_512 = Image.alpha_composite(final_512, bloom_blurred)
final_512.paste(emblem_resized, (bx, by), emblem_resized)

app_icon_png = ASSETS / "app_icon.png"
final_512.save(app_icon_png, format="PNG")
print(f"[2/5] Saved high-res app icon: {app_icon_png}")

# 3. Build multi-resolution Windows ICO with fresh filename qwen_studio.ico
sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
new_ico_path = ASSETS / "qwen_studio.ico"
old_ico_path = ASSETS / "app_icon.ico"
final_512.save(new_ico_path, format="ICO", sizes=sizes)
final_512.save(old_ico_path, format="ICO", sizes=sizes)
print(f"[3/5] Saved multi-resolution Windows ICO to: {new_ico_path} and {old_ico_path}")

# 4. Save favicon for browser/WebView
favicon_path = WEB / "favicon.ico"
final_512.save(favicon_path, format="ICO", sizes=[(64, 64), (32, 32), (16, 16)])
print(f"[4/5] Saved web favicon: {favicon_path}")

print("[5/5] All icon assets built successfully!")

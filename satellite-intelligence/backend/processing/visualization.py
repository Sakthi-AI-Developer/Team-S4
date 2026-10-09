from pathlib import Path

import numpy as np
from PIL import Image

from processing.preprocessing import RasterBand


PALETTES = {
    "ndvi": ((128, 69, 42), (226, 203, 103), (31, 122, 62)),
    "ndwi": ((160, 111, 66), (221, 218, 177), (31, 105, 190)),
    "ndbi": ((49, 111, 73), (216, 206, 146), (170, 75, 64)),
    "change": ((183, 71, 79), (245, 244, 226), (47, 139, 91)),
}


def save_index_png(array: np.ndarray, valid: np.ndarray, path: Path, kind: str) -> None:
    palette = np.asarray(PALETTES[kind], dtype=np.float32)
    image = np.zeros((*array.shape, 4), dtype=np.uint8)
    scaled = np.clip(np.nan_to_num(array, nan=0.0) + 1.0, 0, 2)
    lower = np.floor(scaled).astype(np.int32)
    upper = np.minimum(lower + 1, 2)
    fraction = (scaled - lower)[..., None]
    colors = palette[lower] * (1 - fraction) + palette[upper] * fraction
    image[..., :3] = np.clip(colors, 0, 255).astype(np.uint8)
    image[..., 3] = np.where(valid, 220, 0).astype(np.uint8)
    Image.fromarray(image, mode="RGBA").save(path, optimize=True)


def save_class_png(array: np.ndarray, valid: np.ndarray, path: Path) -> None:
    colors = np.asarray(
        [(195, 154, 75), (55, 133, 79), (54, 132, 197), (179, 75, 64), (177, 143, 110)],
        dtype=np.uint8,
    )
    image = np.zeros((*array.shape, 4), dtype=np.uint8)
    indices = np.clip(array.astype(np.int32) - 1, 0, len(colors) - 1)
    image[..., :3] = colors[indices]
    image[..., 3] = np.where(valid, 220, 0).astype(np.uint8)
    Image.fromarray(image, mode="RGBA").save(path, optimize=True)


def save_rgb_preview(red: RasterBand, green: RasterBand, blue: RasterBand, path: Path) -> dict:
    valid = np.isfinite(red.data) & np.isfinite(green.data) & np.isfinite(blue.data)
    red_values = np.nan_to_num(red.data, nan=0.0)
    green_values = np.nan_to_num(green.data, nan=0.0)
    blue_values = np.nan_to_num(blue.data, nan=0.0)
    red_min = float(np.min(red_values[valid])) if np.any(valid) else 0.0
    green_min = float(np.min(green_values[valid])) if np.any(valid) else 0.0
    blue_min = float(np.min(blue_values[valid])) if np.any(valid) else 0.0
    red_max = float(np.max(red_values[valid])) if np.any(valid) else 1.0
    green_max = float(np.max(green_values[valid])) if np.any(valid) else 1.0
    blue_max = float(np.max(blue_values[valid])) if np.any(valid) else 1.0
    red_range = max(red_max - red_min, 1e-6)
    green_range = max(green_max - green_min, 1e-6)
    blue_range = max(blue_max - blue_min, 1e-6)
    rgb = np.zeros((red.height, red.width, 3), dtype=np.float32)
    rgb[..., 0] = np.clip((red_values - red_min) / red_range, 0.0, 1.0)
    rgb[..., 1] = np.clip((green_values - green_min) / green_range, 0.0, 1.0)
    rgb[..., 2] = np.clip((blue_values - blue_min) / blue_range, 0.0, 1.0)
    rgb[~valid] = 0.0
    image = np.clip(rgb * 255, 0, 255).astype(np.uint8)
    Image.fromarray(image, mode="RGB").save(path, optimize=True)
    return {
        "crs": red.crs or green.crs or blue.crs,
        "width": int(red.width),
        "height": int(red.height),
        "resolution": [float(value) for value in red.resolution],
        "bounds": {
            "west": float(min(red.transform.c, green.transform.c, blue.transform.c)),
            "south": float(min(red.transform.f, green.transform.f, blue.transform.f)),
            "east": float(max(red.transform.c + red.width * red.transform.a, green.transform.c + green.width * green.transform.a, blue.transform.c + blue.width * blue.transform.a)),
            "north": float(max(red.transform.f, green.transform.f, blue.transform.f)),
        },
    }

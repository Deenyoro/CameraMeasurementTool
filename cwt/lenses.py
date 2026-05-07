"""Static database of common camera sensors + lenses.

Used by the FOV editor to translate (sensor, lens_mm, resolution,
mount_height_ft) → DORI distances on the floor plan.
"""

from __future__ import annotations

# Sensor formats. Values are physical sensor dimensions in millimeters,
# the standard quoted values for each "1/x inch" CCTV format.
SENSORS: dict[str, tuple[float, float]] = {
    # name: (width_mm, height_mm)
    '1/4"':   (3.20, 2.40),
    '1/3"':   (4.80, 3.60),
    '1/2.8"': (5.37, 3.02),    # very common in IP cameras (Sony IMX series)
    '1/2.7"': (5.37, 3.78),
    '1/2"':   (6.40, 4.80),
    '1/1.8"': (7.20, 5.40),
    '2/3"':   (8.80, 6.60),
    '1"':     (12.80, 9.60),
}

# Common fixed-focal lenses (mm). Varifocals are user-set inside the range.
LENS_PRESETS_MM: list[float] = [
    1.55, 1.7, 2.0, 2.5, 2.8, 3.0, 3.6, 4.0, 5.0, 6.0, 8.0, 12.0, 16.0, 25.0,
]

# Common camera resolutions
RESOLUTIONS: dict[str, tuple[int, int]] = {
    "720p (HD)":     (1280, 720),
    "1080p (FHD)":   (1920, 1080),
    "3MP":           (2048, 1536),
    "4MP":           (2560, 1440),
    "5MP":           (2592, 1944),
    "4K (UHD)":      (3840, 2160),
    "8MP":           (3840, 2160),
    "12MP":          (4000, 3000),
}

# Quick presets for common installation scenarios.
# Used by the "Quick Preset" buttons in the FOV editor.
INSTALL_PRESETS: dict[str, dict] = {
    "Lobby (wide)":    {"lens_mm": 2.8, "tilt_deg": 15, "mount_height_ft":  9.0},
    "Hallway":         {"lens_mm": 4.0, "tilt_deg":  5, "mount_height_ft":  9.0},
    "Reception desk":  {"lens_mm": 6.0, "tilt_deg": 25, "mount_height_ft":  9.0},
    "Parking lot":     {"lens_mm": 4.0, "tilt_deg": 10, "mount_height_ft": 14.0},
    "Loading dock":    {"lens_mm": 2.8, "tilt_deg": 20, "mount_height_ft": 12.0},
    "Long corridor":   {"lens_mm": 8.0, "tilt_deg":  5, "mount_height_ft":  9.0},
    "Server room":     {"lens_mm": 2.8, "tilt_deg": 30, "mount_height_ft":  8.5},
}

# Default FOV for newly-edited cameras
DEFAULT_FOV: dict = {
    "enabled":         True,
    "lens_mm":         4.0,
    "sensor":          '1/2.8"',
    "resolution":      list(RESOLUTIONS["4K (UHD)"]),
    "mount_height_ft": 9.0,
    "tilt_deg":        15.0,
    "azimuth_deg":     0.0,
    "show":            True,
}

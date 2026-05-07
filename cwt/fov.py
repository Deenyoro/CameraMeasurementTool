"""Camera FOV / DORI math.

Implements the IEC EN 62676-4 DORI (Detection / Observation / Recognition /
Identification) standard for visualizing camera coverage on a floor plan.

DORI thresholds (pixels per meter on target):
    Detection      ≥  25 ppm
    Observation    ≥  62 ppm
    Recognition    ≥ 125 ppm
    Identification ≥ 250 ppm

Given:
    - Sensor width (mm)
    - Lens focal length (mm)
    - Image resolution (pixels)
    - Mount height (ft)  — optional; reduces ground-projected distance
    - Tilt angle (deg)   — optional; downward tilt
    - Azimuth (deg)      — rotation in plan view, 0 = up

Returns the four DORI radii (in inches, projected onto the floor plane)
plus the horizontal angle of view.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from cwt.lenses import SENSORS

# IEC EN 62676-4 thresholds (pixels per meter on target)
DORI_PPM: dict[str, float] = {
    "D": 25,
    "O": 62,
    "R": 125,
    "I": 250,
}

# Display colors per DORI ring (innermost = strictest = darkest red)
DORI_COLORS: dict[str, str] = {
    "D": "#FFE680",   # detection — yellow
    "O": "#FFB347",   # observation — orange
    "R": "#FF5C5C",   # recognition — red
    "I": "#B22222",   # identification — dark red
}

DORI_LABELS: dict[str, str] = {
    "D": "Detection",
    "O": "Observation",
    "R": "Recognition",
    "I": "Identification",
}


@dataclass(frozen=True)
class FOVCone:
    """Result of FOV calculation. Distances are in inches."""
    aov_h_deg: float                 # horizontal angle of view (degrees)
    aov_v_deg: float                 # vertical angle of view (degrees)
    distances_in: dict[str, float]   # DORI radii projected onto floor (inches)
    azimuth_deg: float               # plan-view rotation, 0 = up


def horizontal_aov(sensor_w_mm: float, lens_mm: float) -> float:
    """Horizontal angle of view in degrees, given sensor width and focal length."""
    if lens_mm <= 0 or sensor_w_mm <= 0:
        return 0.0
    return math.degrees(2 * math.atan(sensor_w_mm / (2 * lens_mm)))


def vertical_aov(sensor_h_mm: float, lens_mm: float) -> float:
    if lens_mm <= 0 or sensor_h_mm <= 0:
        return 0.0
    return math.degrees(2 * math.atan(sensor_h_mm / (2 * lens_mm)))


def dori_distance_meters(image_width_px: int, sensor_w_mm: float, lens_mm: float, ppm: float) -> float:
    """Distance at which a target is rendered at `ppm` pixels per meter.

    Derivation: at distance D, the camera sees a horizontal slice of width
    W = 2 * D * tan(AOV/2). The image is `image_width_px` pixels wide, so
    pixel-density on target is image_width_px / W (per meter, since W is in
    meters). Setting that = ppm and solving:
        D = image_width_px / (2 * ppm * tan(AOV/2))
    """
    if lens_mm <= 0 or sensor_w_mm <= 0 or ppm <= 0 or image_width_px <= 0:
        return 0.0
    aov = math.radians(horizontal_aov(sensor_w_mm, lens_mm))
    half = aov / 2
    if half <= 0 or math.isclose(math.tan(half), 0):
        return 0.0
    return image_width_px / (2 * ppm * math.tan(half))


def project_to_floor(line_of_sight_m: float, mount_height_ft: float) -> float:
    """Project a slant distance onto the floor, given camera mount height.

    line_of_sight is the diagonal distance in meters; mount height converts
    via Pythagoras. If line_of_sight < height, the camera can't see that
    far horizontally and we return 0. Result in meters.
    """
    height_m = mount_height_ft * 0.3048
    if line_of_sight_m <= height_m:
        return 0.0
    return math.sqrt(line_of_sight_m ** 2 - height_m ** 2)


def compute_fov(fov: dict) -> FOVCone:
    """Compute FOV geometry from a per-camera FOVSettings dict.

    Returns radii in INCHES (matching the project's image-pixel units after
    multiplying by scale_factor; the renderer handles the conversion).
    """
    sensor = fov.get("sensor", '1/2.8"')
    sensor_w_mm, sensor_h_mm = SENSORS.get(sensor, SENSORS['1/2.8"'])
    lens_mm = float(fov.get("lens_mm", 4.0))
    resolution = fov.get("resolution") or [1920, 1080]
    image_w = int(resolution[0])
    mount_ft = float(fov.get("mount_height_ft", 9.0))

    distances_in: dict[str, float] = {}
    for ring, ppm in DORI_PPM.items():
        d_m = dori_distance_meters(image_w, sensor_w_mm, lens_mm, ppm)
        proj_m = project_to_floor(d_m, mount_ft)
        distances_in[ring] = proj_m * 39.3701  # m -> inches

    return FOVCone(
        aov_h_deg=horizontal_aov(sensor_w_mm, lens_mm),
        aov_v_deg=vertical_aov(sensor_h_mm, lens_mm),
        distances_in=distances_in,
        azimuth_deg=float(fov.get("azimuth_deg", 0.0)),
    )


def cone_polygon(
    cone: FOVCone,
    *,
    center_x: float,
    center_y: float,
    radius_in: float,
    inches_per_pixel: float,
    azimuth_deg: float | None = None,
    n_segments: int = 32,
) -> list[tuple[float, float]]:
    """Build a polygon (in image-pixel coords) for a single DORI ring.

    `inches_per_pixel` converts inches → image pixels (= 1 / scale_factor
    if you've calibrated). `azimuth_deg` is plan-view rotation, 0 = up,
    increasing clockwise.
    """
    if inches_per_pixel <= 0 or radius_in <= 0:
        return []
    radius_px = radius_in / inches_per_pixel
    half_aov = math.radians(cone.aov_h_deg / 2)
    az = math.radians(azimuth_deg if azimuth_deg is not None else cone.azimuth_deg)

    # 0° = up (negative y in image space). Apex is at the camera.
    # Sweep half_aov each side.
    pts: list[tuple[float, float]] = [(center_x, center_y)]
    for i in range(n_segments + 1):
        t = -half_aov + (2 * half_aov) * i / n_segments
        # angle from "up": apply azimuth
        a = t + az
        # In image coords, "up" is -y. Rotation: x = sin(a)*r, y = -cos(a)*r
        x = center_x + math.sin(a) * radius_px
        y = center_y - math.cos(a) * radius_px
        pts.append((x, y))
    pts.append((center_x, center_y))
    return pts

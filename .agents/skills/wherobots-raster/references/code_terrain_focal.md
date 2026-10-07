# Reference code: terrain and focal functions (numpy/scipy)

Pure functions on a 2-D float32 array (row 0 north, nodata already NaN) and cell sizes in
**metres**. They run anywhere: driver, notebook, or inside a raster UDF (`code_sedona_udfs.md` wraps
them). Formulas, sign conventions and cluster validation are in `recipes_terrain.md`.

Unit-tested locally on synthetic surfaces (14 checks: tilted planes for slope and the eight aspect
orientations, hillshade on flat and sun-facing planes, TRI/roughness closed forms, TPI on a Gaussian
ridge, landform classes, curvature on a cylinder and a bowl, focal stats on a ramp, every index on
scalar inputs, halo-vs-single-pass seam equivalence, per-row cell sizes, window clipping, FFT vs
direct focal mean); last run **2026-10-07**, 14/14 pass. Cluster check 2026-09-30: tiled halo UDFs
vs a single pass over 1.03 M cells of USGS 3DEP 1/3 arc-second, slope max 0.009 deg (float32
rounding), TRI/roughness/TPI exactly 0.

Every focal function needs a halo of its radius when run per tile (`HALO`, and `edge_effects.md`).

```python
import math

import numpy as np
from scipy import ndimage

# Halo width (cells) each focal operation needs on every side of a tile.
HALO = {
    "slope": 1, "aspect": 1, "hillshade": 1, "curvature": 1, "tri": 1, "roughness_3x3": 1,
    "tpi": "radius", "focal_stat": "size // 2", "multidirectional_hillshade": 1,
}

# ----------------------------------------------------------------------------- cell size


def cell_size_m(scale_x, scale_y, center_lat_deg=None, projected=True):
    """Cell size in metres from the geotransform scale terms.

    projected=True: the scales are already metres (UTM, Albers, ...). Otherwise treat them as
    degrees and convert at `center_lat_deg` (ellipsoidal metres per degree, WGS84 series).
    """
    sx, sy = abs(scale_x), abs(scale_y)
    if projected:
        return sx, sy
    lat = math.radians(center_lat_deg)
    m_lat = 111_132.954 - 559.822 * math.cos(2 * lat) + 1.175 * math.cos(4 * lat)
    m_lon = 111_412.84 * math.cos(lat) - 93.5 * math.cos(3 * lat)
    return sx * m_lon, sy * m_lat


def row_cell_sizes(ip_y, scale_x, scale_y, n_rows, pad=0):
    """Metric (dx, dy) per row of a GEOGRAPHIC array, shaped (n_rows, 1) so they broadcast in every
    gradient function. Row 0 is the first halo row (pad rows above the tile's own first row).
    Per-row instead of per-tile-centre: on a 1024-px 1 arc-second tile dx drifts about 0.3 %
    across the tile at 45 N; per-row cell sizes also make tiled output equal single-pass output."""
    r = np.arange(n_rows, dtype=np.float64) - pad
    lat = np.radians(ip_y + scale_y * (r + 0.5))
    m_lat = 111_132.954 - 559.822 * np.cos(2 * lat) + 1.175 * np.cos(4 * lat)
    m_lon = 111_412.84 * np.cos(lat) - 93.5 * np.cos(3 * lat)
    return ((abs(scale_x) * m_lon).astype(np.float32)[:, None],
            (abs(scale_y) * m_lat).astype(np.float32)[:, None])


def clip_window(col0, row0, width, height, file_width, file_height):
    """Intersect a pixel window (may start negative / run past the file) with a file of the given size.
    Returns (src_col, src_row, w, h, dst_col, dst_row) or None when they do not overlap: read
    `w x h` at (src_col, src_row) in the file and paste it at (dst_col, dst_row) in the window."""
    c0, r0 = max(col0, 0), max(row0, 0)
    c1, r1 = min(col0 + width, file_width), min(row0 + height, file_height)
    if c1 <= c0 or r1 <= r0:
        return None
    return c0, r0, c1 - c0, r1 - r0, c0 - col0, r0 - row0


# ----------------------------------------------------------------------------- gradients

_KX = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)   # east - west
_KY = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=np.float32)   # south - north


def horn_gradient(z, dx, dy):
    """Horn (1981) 3x3 partial derivatives. Returns (dzdx, dzdy) in metre/metre.
    Edges use nearest-neighbour padding: correct only if `z` already carries a 1-cell halo."""
    z = np.asarray(z, dtype=np.float32)
    dzdx = ndimage.correlate(z, _KX, mode="nearest") / (8.0 * dx)
    dzdy = ndimage.correlate(z, _KY, mode="nearest") / (8.0 * dy)
    return dzdx, dzdy


def slope(z, dx, dy, units="degrees"):
    """Slope from Horn gradients. units: 'degrees', 'percent' (100 * rise/run) or 'radians'."""
    dzdx, dzdy = horn_gradient(z, dx, dy)
    rise = np.hypot(dzdx, dzdy)
    if units == "percent":
        return (100.0 * rise).astype(np.float32)
    rad = np.arctan(rise)
    return (rad if units == "radians" else np.degrees(rad)).astype(np.float32)


def _aspect_rad(dzdx, dzdy):
    """Math-convention aspect (radians, counter-clockwise from east) per ESRI, before compass conversion."""
    a = np.arctan2(dzdy, -dzdx)
    a = np.where(a < 0, a + 2 * np.pi, a)
    return a


def aspect(z, dx, dy, flat_value=-1.0, flat_tolerance=0.0):
    """Compass aspect in degrees (0 = north, 90 = east, clockwise) of the downslope direction.
    Cells with gradient magnitude <= flat_tolerance get `flat_value`."""
    dzdx, dzdy = horn_gradient(z, dx, dy)
    deg = np.degrees(np.arctan2(dzdy, -dzdx))
    compass = np.where(deg < 0, 90.0 - deg, np.where(deg > 90.0, 360.0 - deg + 90.0, 90.0 - deg))
    compass = np.where(compass >= 360.0, compass - 360.0, compass)
    flat = np.hypot(dzdx, dzdy) <= flat_tolerance
    out = np.where(flat, flat_value, compass)
    out = np.where(np.isnan(dzdx) | np.isnan(dzdy), np.nan, out)
    return out.astype(np.float32)


def hillshade(z, dx, dy, azimuth=315.0, altitude=45.0, z_factor=1.0):
    """Analytical hillshade 0-255 (float32), ESRI/GDAL formula, sun azimuth clockwise from north."""
    dzdx, dzdy = horn_gradient(z, dx, dy)
    dzdx, dzdy = dzdx * z_factor, dzdy * z_factor
    slope_r = np.arctan(np.hypot(dzdx, dzdy))
    asp = _aspect_rad(dzdx, dzdy)
    zen = math.radians(90.0 - altitude)
    az_math = math.radians((360.0 - azimuth + 90.0) % 360.0)
    hs = 255.0 * (math.cos(zen) * np.cos(slope_r) + math.sin(zen) * np.sin(slope_r) * np.cos(az_math - asp))
    return np.clip(hs, 0, 255).astype(np.float32)


def hillshade_multidirectional(z, dx, dy, altitude=45.0, z_factor=1.0, azimuths=(225.0, 270.0, 315.0, 360.0)):
    """Mark (1992) multidirectional hillshade as in gdaldem -multidirectional: four suns weighted by
    sin^2(aspect - azimuth). Flat cells get the plain mean. Untested against gdaldem output."""
    dzdx, dzdy = horn_gradient(z, dx, dy)
    asp = _aspect_rad(dzdx * z_factor, dzdy * z_factor)
    num = np.zeros_like(asp, dtype=np.float64)
    den = np.zeros_like(asp, dtype=np.float64)
    for az in azimuths:
        w = np.sin(asp - math.radians(az)) ** 2
        num += w * hillshade(z, dx, dy, az, altitude, z_factor)
        den += w
    flat = den < 1e-12
    mean = sum(hillshade(z, dx, dy, az, altitude, z_factor) for az in azimuths) / len(azimuths)
    out = np.where(flat, mean, num / np.where(flat, 1.0, den))
    return out.astype(np.float32)


# ----------------------------------------------------------------------------- ruggedness, position, roughness


def tri(z, method="riley"):
    """Terrain Ruggedness Index on a 3x3 window.
    'riley' (Riley et al. 1999): sqrt(sum((z_n - z_c)^2)) over the 8 neighbours (also gdaldem -alg Riley).
    'wilson': mean |z_n - z_c| (gdaldem default). Both are 0 on a flat plane."""
    z = np.asarray(z, dtype=np.float32)
    acc = np.zeros_like(z, dtype=np.float64)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr or dc:
                n = _shift(z, dr, dc)
                d = n - z
                acc += d * d if method == "riley" else np.abs(d)
    out = np.sqrt(acc) if method == "riley" else acc / 8.0
    return out.astype(np.float32)


def _shift(z, dr, dc):
    """z shifted so that out[r, c] = z[r + dr, c + dc], edge-replicated."""
    p = np.pad(z, 1, mode="edge")
    return p[1 + dr: 1 + dr + z.shape[0], 1 + dc: 1 + dc + z.shape[1]]


def _disk(radius, inner=0):
    r = int(radius)
    yy, xx = np.mgrid[-r: r + 1, -r: r + 1]
    d2 = xx * xx + yy * yy
    return ((d2 <= r * r) & (d2 > inner * inner)).astype(np.float32)


def nan_focal_mean(z, footprint):
    """Mean over `footprint` ignoring NaN (sum of valid / count of valid)."""
    z = np.asarray(z, dtype=np.float32)
    valid = np.isfinite(z)
    zz, vv = np.where(valid, z, 0.0).astype(np.float64), valid.astype(np.float64)
    if footprint.size > 225:
        # FFT for large windows (TPI r >= 8): direct correlation is O(N K) and takes minutes per
        # 1024-px tile at r = 67; zero padding matches mode="constant", cval=0. Flip = correlation.
        from scipy.signal import fftconvolve

        fp = np.asarray(footprint, dtype=np.float64)[::-1, ::-1]
        s = fftconvolve(zz, fp, mode="same")
        n = np.rint(fftconvolve(vv, fp, mode="same"))   # integer counts; rint removes FFT noise
    else:
        s = ndimage.correlate(zz, footprint, mode="constant", cval=0.0)
        n = ndimage.correlate(vv, footprint, mode="constant", cval=0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0.5, s / n, np.nan)


def tpi(z, radius, inner_radius=0):
    """Topographic Position Index (Weiss 2001): z - mean(z over a disk of `radius` cells,
    centre excluded; `inner_radius` > 0 makes it an annulus). Positive = ridge/top,
    negative = valley/toe, near 0 = flat or mid-slope. Needs a halo of `radius` cells."""
    fp = _disk(radius, inner_radius)
    fp[radius, radius] = 0.0
    return (np.asarray(z, dtype=np.float32) - nan_focal_mean(z, fp)).astype(np.float32)


def landform_class(tpi_small, tpi_large, slope_deg, sd_small, sd_large, mean_small=0.0, mean_large=0.0,
                   slope_flat_deg=5.0, z=1.0):
    """Weiss (2001) two-scale landform classification, 10 classes (uint8):
      1 canyons/deeply incised streams, 2 midslope drainages, 3 upland drainages/headwaters,
      4 U-shaped valleys, 5 plains, 6 open slopes, 7 upper slopes/mesas, 8 local ridges in valleys,
      9 midslope ridges, 10 mountain tops/high ridges; 0 = nodata.
    TPI is standardised with SCENE-WIDE mean/sd (pass them in; tile-local values break comparability
    across tiles, exactly like the two-pass rule for LISA)."""
    s = (tpi_small - mean_small) / sd_small
    l = (tpi_large - mean_large) / sd_large
    out = np.zeros(s.shape, dtype=np.uint8)
    ok = np.isfinite(s) & np.isfinite(l) & np.isfinite(slope_deg)
    flat = slope_deg <= slope_flat_deg
    out[ok & (s <= -z) & (l <= -z)] = 1
    out[ok & (s <= -z) & (l > -z) & (l < z)] = 2
    out[ok & (s <= -z) & (l >= z)] = 3
    out[ok & (s > -z) & (s < z) & (l <= -z)] = 4
    out[ok & (s > -z) & (s < z) & (l > -z) & (l < z) & flat] = 5
    out[ok & (s > -z) & (s < z) & (l > -z) & (l < z) & ~flat] = 6
    out[ok & (s > -z) & (s < z) & (l >= z)] = 7
    out[ok & (s >= z) & (l <= -z)] = 8
    out[ok & (s >= z) & (l > -z) & (l < z)] = 9
    out[ok & (s >= z) & (l >= z)] = 10
    return out


def roughness(z, size=3):
    """Range (max - min) in a size x size window (gdaldem roughness for size=3). NaN-aware."""
    z = np.asarray(z, dtype=np.float32)
    hi = ndimage.maximum_filter(np.where(np.isfinite(z), z, -np.inf), size=size, mode="nearest")
    lo = ndimage.minimum_filter(np.where(np.isfinite(z), z, np.inf), size=size, mode="nearest")
    out = hi - lo
    out[~np.isfinite(z)] = np.nan
    return out.astype(np.float32)


# ----------------------------------------------------------------------------- curvature


def curvature(z, dx, dy, method="zevenbergen"):
    """Profile and plan curvature (1/m) from a 3x3 quadratic fit z = Dx^2 + Ey^2 + Fxy + Gx + Hy + I.
    method 'zevenbergen' (Zevenbergen & Thorne 1987, exact fit through the 4-neighbours) or
    'evans' (Evans 1979 / Young, least-squares over all 9 cells; smoother).
    Sign: for z = a*x^2 the profile curvature is -2a, so a valley cross-section (a > 0) is negative
    and a ridge (a < 0) positive; plan curvature has the opposite sign for the same bowl/dome.
    ArcGIS multiplies by -100 for its 'curvature' tool; check the convention before comparing.
    Flat cells (G = H = 0) return 0."""
    z = np.asarray(z, dtype=np.float32).astype(np.float64)
    Z1, Z2, Z3 = _shift(z, -1, -1), _shift(z, -1, 0), _shift(z, -1, 1)   # north row
    Z4, Z5, Z6 = _shift(z, 0, -1), z, _shift(z, 0, 1)
    Z7, Z8, Z9 = _shift(z, 1, -1), _shift(z, 1, 0), _shift(z, 1, 1)      # south row
    if method == "zevenbergen":
        D = ((Z4 + Z6) / 2.0 - Z5) / (dx * dx)
        E = ((Z2 + Z8) / 2.0 - Z5) / (dy * dy)
        F = (-Z1 + Z3 + Z7 - Z9) / (4.0 * dx * dy)
        G = (-Z4 + Z6) / (2.0 * dx)
        H = (Z2 - Z8) / (2.0 * dy)
    elif method == "evans":
        D = ((Z1 + Z3 + Z4 + Z6 + Z7 + Z9) - 2.0 * (Z2 + Z5 + Z8)) / (6.0 * dx * dx)
        E = ((Z1 + Z2 + Z3 + Z7 + Z8 + Z9) - 2.0 * (Z4 + Z5 + Z6)) / (6.0 * dy * dy)
        F = (Z3 + Z7 - Z1 - Z9) / (4.0 * dx * dy)
        G = (Z3 + Z6 + Z9 - Z1 - Z4 - Z7) / (6.0 * dx)
        H = (Z1 + Z2 + Z3 - Z7 - Z8 - Z9) / (6.0 * dy)
    else:
        raise ValueError(method)
    g2 = G * G + H * H
    with np.errstate(invalid="ignore", divide="ignore"):
        prof = np.where(g2 > 0, -2.0 * (D * G * G + E * H * H + F * G * H) / g2, 0.0)
        plan = np.where(g2 > 0, 2.0 * (D * H * H + E * G * G - F * G * H) / g2, 0.0)
    nan = ~np.isfinite(z)
    prof[nan] = np.nan
    plan[nan] = np.nan
    return prof.astype(np.float32), plan.astype(np.float32)


# ----------------------------------------------------------------------------- generic focal statistic


def focal_stat(z, size=3, stat="mean", footprint=None):
    """Generic NaN-aware focal statistic over a size x size square (or a boolean `footprint`).
    stat: mean, std, min, max, range, median, count. Needs a halo of size // 2 cells."""
    z = np.asarray(z, dtype=np.float32)
    fp = np.ones((size, size), dtype=np.float32) if footprint is None else footprint.astype(np.float32)
    valid = np.isfinite(z)
    if stat in ("mean", "std", "count"):
        z0 = np.where(valid, z, 0.0).astype(np.float64)
        n = ndimage.correlate(valid.astype(np.float64), fp, mode="constant", cval=0.0)
        s = ndimage.correlate(z0, fp, mode="constant", cval=0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            if stat == "count":
                return n.astype(np.float32)
            mean = np.where(n > 0, s / n, np.nan)
            if stat == "mean":
                return mean.astype(np.float32)
            s2 = ndimage.correlate(z0 * z0, fp, mode="constant", cval=0.0)
            var = np.where(n > 0, s2 / n - mean * mean, np.nan)
            return np.sqrt(np.clip(var, 0, None)).astype(np.float32)
    if stat in ("min", "max", "range"):
        hi = ndimage.maximum_filter(np.where(valid, z, -np.inf), footprint=fp > 0, mode="nearest")
        lo = ndimage.minimum_filter(np.where(valid, z, np.inf), footprint=fp > 0, mode="nearest")
        out = {"min": lo, "max": hi, "range": hi - lo}[stat]
        out = np.where(np.isfinite(out), out, np.nan)
        return out.astype(np.float32)
    if stat == "median":
        return ndimage.generic_filter(z, np.nanmedian, footprint=fp > 0, mode="nearest").astype(np.float32)
    raise ValueError(stat)
```

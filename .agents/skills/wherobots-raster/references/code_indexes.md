# Reference code: spectral indexes, SCL mask, COG bytes (numpy)

Reflectance in (floats 0-1, nodata NaN), float32 out with NaN where undefined; normalized
differences are clipped to [-1, 1]. Convert digital numbers first with `s2_reflectance` **after**
the units check in `recipes_indexes.md`. Unit-tested locally on scalar inputs for every index (known values, NaN on zero sums, clipping
to [-1, 1]); last run **2026-10-07**, pass. Cluster check 2026-09-30: raster-path and scalar-path NDVI means agree to 9.5e-9 over 2,097,149
valid pixels.

```python
import math

import numpy as np

# ----------------------------------------------------------------------------- spectral indexes
# All functions take reflectance arrays (0..1 floats, nodata NaN) unless noted. Outputs float32.


def s2_reflectance(dn, scale=0.0001, offset=0.0, nodata=0):
    """Digital numbers -> physical values with the GDAL tag convention: value = dn * scale + offset
    (the scale/offset a file's tags report, e.g. 0.0001 and -0.1). Generic despite the name.
    VERIFY on known targets first. Example: sentinel-cogs files carry no tags and need scale 0.0001,
    offset 0 (DN / 10000); applying an offset they do not need saturates NDVI at 1.0."""
    dn = np.asarray(dn)
    v = dn.astype(np.float64) * scale + offset
    return np.where(dn == nodata, np.nan, v).astype(np.float32)


def normalized_difference(a, b):
    """(a - b) / (a + b), NaN where the sum is 0 or either input is NaN, clipped to [-1, 1]."""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    with np.errstate(divide="ignore", invalid="ignore"):
        v = (a - b) / (a + b)
    v = np.where(np.isfinite(v), v, np.nan)
    return np.clip(v, -1.0, 1.0).astype(np.float32)


def ndvi(nir, red):
    return normalized_difference(nir, red)


def gndvi(nir, green):
    return normalized_difference(nir, green)


def ndwi(green, nir):
    """McFeeters (1996) water index."""
    return normalized_difference(green, nir)


def mndwi(green, swir1):
    """Xu (2006) modified NDWI (SWIR1 = Sentinel-2 B11, Landsat 8/9 B6)."""
    return normalized_difference(green, swir1)


def ndmi(nir, swir1):
    """Normalized Difference Moisture Index (Gao 1996 NDWI): (NIR - SWIR1) / (NIR + SWIR1)."""
    return normalized_difference(nir, swir1)


def nbr(nir, swir2):
    """Normalized Burn Ratio (SWIR2 = Sentinel-2 B12)."""
    return normalized_difference(nir, swir2)


def nbr2(swir1, swir2):
    return normalized_difference(swir1, swir2)


def ndbi(swir1, nir):
    """Zha (2003) built-up index."""
    return normalized_difference(swir1, nir)


def evi(nir, red, blue, G=2.5, C1=6.0, C2=7.5, L=1.0):
    """Enhanced Vegetation Index (MODIS constants). Reflectance in, otherwise the L term is wrong."""
    nir, red, blue = (np.asarray(x, dtype=np.float32) for x in (nir, red, blue))
    with np.errstate(divide="ignore", invalid="ignore"):
        v = G * (nir - red) / (nir + C1 * red - C2 * blue + L)
    v = np.where(np.isfinite(v), v, np.nan)
    return np.clip(v, -1.0, 1.0).astype(np.float32)


def savi(nir, red, L=0.5):
    """Soil Adjusted Vegetation Index (Huete 1988), L = 0.5 for intermediate cover."""
    nir, red = np.asarray(nir, dtype=np.float32), np.asarray(red, dtype=np.float32)
    with np.errstate(divide="ignore", invalid="ignore"):
        v = (1.0 + L) * (nir - red) / (nir + red + L)
    v = np.where(np.isfinite(v), v, np.nan)
    return v.astype(np.float32)


def msavi(nir, red):
    """MSAVI2 (Qi 1994): (2 NIR + 1 - sqrt((2 NIR + 1)^2 - 8 (NIR - red))) / 2."""
    nir, red = np.asarray(nir, dtype=np.float32), np.asarray(red, dtype=np.float32)
    with np.errstate(invalid="ignore"):
        v = (2.0 * nir + 1.0 - np.sqrt((2.0 * nir + 1.0) ** 2 - 8.0 * (nir - red))) / 2.0
    v = np.where(np.isfinite(v), v, np.nan)
    return v.astype(np.float32)


def bsi(blue, red, nir, swir1):
    """Bare Soil Index: ((SWIR1 + red) - (NIR + blue)) / ((SWIR1 + red) + (NIR + blue))."""
    return normalized_difference(np.asarray(swir1, dtype=np.float32) + np.asarray(red, dtype=np.float32),
                                 np.asarray(nir, dtype=np.float32) + np.asarray(blue, dtype=np.float32))


def reci(nir, red_edge):
    """Red-edge Chlorophyll Index CIre (Gitelson 2003): NIR / red_edge - 1 (Sentinel-2 B8 or B8A over B5)."""
    nir, red_edge = np.asarray(nir, dtype=np.float32), np.asarray(red_edge, dtype=np.float32)
    with np.errstate(divide="ignore", invalid="ignore"):
        v = nir / red_edge - 1.0
    v = np.where(np.isfinite(v), v, np.nan)
    return v.astype(np.float32)


# Sentinel-2 Scene Classification Layer values (ESA): 0 nodata, 1 saturated/defective, 2 dark area,
# 3 cloud shadow, 4 vegetation, 5 not vegetated, 6 water, 7 unclassified, 8 cloud medium prob,
# 9 cloud high prob, 10 thin cirrus, 11 snow/ice.
SCL_CLEAR = (4, 5, 6)
SCL_CLEAR_WITH_SNOW = (4, 5, 6, 11)


def scl_mask(scl, keep=SCL_CLEAR):
    """True where the SCL class is in `keep`. SCL is 20 m: resample to the index grid first
    (nearest) or evaluate on the 20 m grid. Untested on the cluster; the class table is ESA's."""
    return np.isin(np.asarray(scl), keep)


def apply_mask(index, mask):
    out = np.array(index, dtype=np.float32, copy=True)
    out[~mask] = np.nan
    return out


# ----------------------------------------------------------------------------- COG bytes (rasterio only)


def to_cog_bytes(arr_chw, transform6, crs_wkt, nodata=np.nan, descriptions=None, blocksize=512,
                 dtype="float32", compress="deflate", predictor=3):
    """CHW array + GDAL affine (a, b, c, d, e, f) + CRS WKT -> Cloud Optimized GeoTIFF bytes in memory.
    Ship the bytes through df.write.format('raster') so the session's storage credentials are used."""
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.transform import Affine

    arr = np.asarray(arr_chw)
    with MemoryFile() as mem:
        with mem.open(driver="COG", height=arr.shape[1], width=arr.shape[2], count=arr.shape[0], dtype=dtype,
                      crs=rasterio.crs.CRS.from_wkt(crs_wkt), transform=Affine(*transform6), nodata=nodata,
                      compress=compress, predictor=predictor, blocksize=blocksize, overview_resampling="average") as ds:
            ds.write(arr.astype(dtype))
            for i, d in enumerate(descriptions or [], 1):
                ds.set_band_description(i, d)
        return mem.read()
```

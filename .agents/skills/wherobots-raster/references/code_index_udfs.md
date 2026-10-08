# Reference code: spectral index UDFs

Paste **after** `code_sedona_udfs.md`: it reuses that file's runtime check (`HAVE_SEDONA`) and imports.
The `S2_SCALE`/`S2_OFFSET` constants are an example for one source: set them from the units check in
`recipes_indexes.md` for yours. Out-db arguments arrive as raw DN.

> `ndvi_raster_udf`, `ndvi_sum_count_udf`, `ndmi_from_stack_udf` validated on Wherobots Cloud 2026-09-30
> (raster-path and scalar-path NDVI means agree to 9.5e-9); `ndvi_sum_count_in_polygon_udf` ran green in the
> 2026-10-07 naive-user test (200 polygons, pixel count equal to `RS_ZonalStats` count). `ndvi_outdb_udf`
> (empty template) is *untested on the cluster*; it is `ndvi_raster_udf` with the template pattern that
> measured about 40 % faster for the terrain UDFs.

```python
if HAVE_SEDONA:

    # ---- spectral index UDFs. Out-db arrives as raw DN -> convert with s2_reflectance (defaults: sentinel-cogs).
    S2_SCALE, S2_OFFSET = 10000.0, 0.0   # EXAMPLE values (sentinel-cogs files); set per source after the units check

    @sedona_vectorized_udf(return_type=RasterType())
    def ndvi_outdb_udf(template: SedonaRaster, red_outdb: SedonaRaster, nir_outdb: SedonaRaster) -> SedonaRaster:
        """Raster out with an EMPTY template (empty_template_sql on the red tile) and both bands out-db:
        no JVM pixel copy. Set nodata afterwards with RS_SetBandNoDataValue(r, 1, ...)."""
        r = s2_reflectance(red_outdb.as_numpy()[0], S2_SCALE, S2_OFFSET)
        n = s2_reflectance(nir_outdb.as_numpy()[0], S2_SCALE, S2_OFFSET)
        return template.with_bands(ndvi(n, r)[np.newaxis])

    @sedona_vectorized_udf(return_type=RasterType())
    def ndvi_raster_udf(red_indb: SedonaRaster, nir_outdb: SedonaRaster) -> SedonaRaster:
        """Raster out: RS_AsInDB(red) template + out-db nir. Set nodata afterwards with RS_SetBandNoDataValue."""
        r = s2_reflectance(red_indb.as_numpy()[0], S2_SCALE, S2_OFFSET)
        n = s2_reflectance(nir_outdb.as_numpy()[0], S2_SCALE, S2_OFFSET)
        return red_indb.with_bands(ndvi(n, r)[np.newaxis])

    @sedona_vectorized_udf(return_type=RasterType())
    def ndmi_from_stack_udf(stack_indb: SedonaRaster) -> SedonaRaster:
        """RS_StackTileExplode(ARRAY(nir, swir16), ref, w, h) tile -> NDMI. Band order = array order."""
        a = stack_indb.as_numpy()
        n, s = s2_reflectance(a[0], S2_SCALE, S2_OFFSET), s2_reflectance(a[1], S2_SCALE, S2_OFFSET)
        return stack_indb.with_bands(ndmi(n, s)[np.newaxis])

    @sedona_vectorized_udf(return_type=ArrayType(DoubleType()))
    def ndvi_sum_count_udf(red_outdb: SedonaRaster, nir_outdb: SedonaRaster) -> list:
        """Scalar out, both out-db (zero JVM pixel copies): [sum, count] of valid NDVI. Aggregate in Spark."""
        v = ndvi(s2_reflectance(nir_outdb.as_numpy()[0], S2_SCALE, S2_OFFSET),
                 s2_reflectance(red_outdb.as_numpy()[0], S2_SCALE, S2_OFFSET))
        ok = np.isfinite(v)
        return [float(v[ok].sum()), float(ok.sum())]

    def geometry_mask(raster: "SedonaRaster", geom, all_touched: bool = False) -> np.ndarray:
        """Boolean (H, W) mask, True inside `geom`; geom must already be in the raster CRS."""
        from rasterio import features
        from rasterio.transform import Affine

        return features.geometry_mask([geom], out_shape=(raster.height, raster.width),
                                      transform=Affine(*affine6_of(raster)), invert=True, all_touched=all_touched)

    @sedona_vectorized_udf(return_type=ArrayType(DoubleType()))
    def ndvi_sum_count_in_polygon_udf(red: SedonaRaster, nir: SedonaRaster, geom: BaseGeometry) -> list:
        """[sum, count] of NDVI inside the polygon on a bbox window (RS_Clip by ST_Envelope first).
        Aggregate sum/count across (field, tile) pairs in Spark for exact per-field means."""
        v = ndvi(s2_reflectance(nir.as_numpy()[0], S2_SCALE, S2_OFFSET),
                 s2_reflectance(red.as_numpy()[0], S2_SCALE, S2_OFFSET))
        ok = geometry_mask(red, geom) & np.isfinite(v)
        return [float(v[ok].sum()), float(ok.sum())]
```

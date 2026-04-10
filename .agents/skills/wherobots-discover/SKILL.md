---
name: wherobots-discover
description: Use when the Wherobots catalog does not contain the needed dataset — covers discovering and ingesting external geospatial data from STAC APIs (Element84 Earth Search, Microsoft Planetary Computer) into Wherobots via Apache Sedona.
---

# Wherobots Discover

## ⚠️ Mandatory Goal

The purpose of this skill is to get external data **into Wherobots** so all computation happens server-side in Sedona. The workflow is not complete until the data is registered as a Wherobots view or table and verified with `execute_query_tool`.

**Do NOT fall back to local computation** (rasterio, numpy, geopandas, pandas). Local processing defeats the purpose — Wherobots exists to run spatial computation at scale, server-side. If you find yourself installing Python packages and running local scripts, stop and return to this workflow.

## When to Use

Use this skill when `list_tables_tool` / `list_hierarchy_tool` confirms the needed dataset is not in any Wherobots catalog. External STAC sources are the primary fallback for satellite imagery, land cover, elevation, and other public Earth observation data.

## STAC API Sources

| Source | Base URL | Strengths |
|---|---|---|
| Element84 Earth Search | `https://earth-search.aws.element84.com/v1` | Sentinel-2, Landsat, NAIP — all on AWS S3, no egress cost in us-east-1 |
| Microsoft Planetary Computer | `https://planetarycomputer.microsoft.com/api/stac/v1` | Broader catalog (Sentinel, Landsat, MODIS, NOAA, USGS); requires token signing for asset access |

## Session Architecture — Read This First

The MCP `execute_query_tool` and a Wherobots Notebook run in **separate, isolated Spark sessions**. This has two critical consequences:

- `execute_query_tool` runs **SQL SELECT only** — it cannot execute Python, PySpark, or DDL (`CREATE TABLE`, `CREATE VIEW`). Attempting any of these will be blocked.
- Temporary views created in a notebook (`df.createOrReplaceTempView(...)`) are **invisible** to the MCP SQL session. They do not cross session boundaries.

The only data that `execute_query_tool` can query is data stored in a **persistent Havasu (Iceberg) table**. The workflow therefore has two distinct phases that must not be conflated:

| Phase | Where it runs | What it does |
|---|---|---|
| Discover + Ingest | Wherobots Notebook (Python/PySpark) | Find data, load into Sedona, write to Havasu |
| Verify + Query | MCP `execute_query_tool` (SQL only) | SELECT from the persisted Havasu table |

## Mandatory Workflow

Complete every step in order. Do not skip steps or short-circuit to local computation.

### Step 1: Discover — query STAC APIs directly

**Do NOT substitute web search or a research agent for this step.** Web search tells you that a collection exists by name. STAC API queries tell you whether items actually exist for your specific bounding box and datetime range, how many there are, and what asset URLs to load. You need the latter before you can write any notebook code. Run these queries yourself now, before writing any code.

Query both catalogs. Start with Element84 (no auth required), then Planetary Computer:

```python
import pystac_client, planetary_computer

# Element84 Earth Search — Sentinel-2, Landsat, NAIP (no signing required)
e84 = pystac_client.Client.open("https://earth-search.aws.element84.com/v1")

# Microsoft Planetary Computer — MODIS, NOAA, USGS (requires signing)
pc = pystac_client.Client.open(
    "https://planetarycomputer.microsoft.com/api/stac/v1",
    modifier=planetary_computer.sign_inplace,
)

# List all collections to find relevant ones
for c in pc.get_collections():
    print(c.id, "-", c.description)
```

Search for items covering your specific area and time range:

```python
bbox = [-118.9, 34.0, -118.0, 34.25]   # [west, south, east, north] — your AOI

search = pc.search(
    collections=["modis-64A1-061"],     # replace with your target collection
    bbox=bbox,
    datetime="2025-01-01/2025-03-31",
    max_items=50,
)
items = list(search.items())
print(f"Found {len(items)} items")
for item in items:
    print(item.id, list(item.assets.keys()))
```

If `len(items) == 0`, the collection has no coverage for your bbox/datetime — try a different collection or source. Do not proceed to Step 2 until you have confirmed items exist and have inspected their asset keys.

### Step 2: Load into Sedona — run in a Wherobots Notebook

#### Vector data (GeoJSON, GeoParquet)

```python
from sedona.spark import SedonaContext
from pyspark.sql import SparkSession
from pyspark.sql.functions import expr

sedona = SedonaContext.create(SparkSession.builder.getOrCreate())

# GeoParquet (preferred — preserves geometry types natively)
df = sedona.read.format("geoparquet").load("s3://bucket/path/file.parquet")

# GeoJSON from a public URL or S3
df = sedona.read.format("geojson").load("https://example.com/data.geojson")
df = df.withColumn("geometry", expr("ST_GeomFromGeoJSON(geometry)"))
```

#### STAC collection via Sedona's built-in STAC reader

```python
df = sedona.read.format("stac") \
    .option("itemsLimitMax", "1000") \
    .load("https://earth-search.aws.element84.com/v1/collections/sentinel-2-c1-l2a")
```

#### Raster data (Cloud-Optimized GeoTIFF)

```python
from pyspark.sql.functions import expr

urls = [item.assets["B04"].href for item in items]

raster_df = (
    sedona.read
    .format("raster")
    .option("dropInvalid", True)
    .load(urls)
)
```

Sedona's `RS_*` functions operate on raster columns — use `search_documentation_tool` with query `"RS_ raster functions"` to find the right one before writing raster SQL.

### Step 3: Persist to a Havasu table — run in a Wherobots Notebook

**Do not use `createOrReplaceTempView`.** Temp views are session-scoped and will not be visible to `execute_query_tool`. Always write to a persistent Havasu (Iceberg) table:

```python
# Create the database if it doesn't exist
sedona.sql("CREATE DATABASE IF NOT EXISTS wherobots_managed.my_db")

# Write as a persistent Havasu table
df.writeTo("wherobots_managed.my_db.my_table").createOrReplace()
```

### Step 4: Verify via `execute_query_tool` — MCP SQL session

Only after the Havasu table is written can you use the MCP tools. Call `execute_query_tool` with a sanity-check SELECT to confirm the data is accessible:

```sql
SELECT COUNT(*), ST_AsText(ST_Envelope(ST_Union(geometry))) AS bbox
FROM wherobots_managed.my_db.my_table
```

The workflow is complete only when this query returns correct results. Only then should you generate spatial queries or write job code against this dataset.

## Non-Obvious Constraints

- **Planetary Computer asset signing**: Unsigned COG URLs from Planetary Computer return 403. Always apply `planetary_computer.sign_inplace` modifier or call `planetary_computer.sign(item)` before extracting `.href`.
- **Earth Search S3 egress**: Assets are in `us-east-1`. Running Wherobots jobs in `us-west-2` incurs cross-region data transfer. Prefer Earth Search for small queries; for large batch jobs, prefer Planetary Computer if the same data is available there.
- **pystac-client vs pystac**: `pystac` is the object model; `pystac_client` is the search client. Import both only if you need to construct items manually. For catalog search, `pystac_client` is sufficient.
- **COG partial reads**: COGs support HTTP range requests — Sedona's raster reader exploits this to read only the needed overview level. Avoid downloading full files before passing to Sedona.

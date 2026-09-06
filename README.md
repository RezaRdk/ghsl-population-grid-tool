# GHSL Population Grid Tool (ArcGIS Pro Python Toolbox)

An ArcGIS Pro Python Toolbox (`.pyt`) that automates building a **population
vector grid** from JRC's [GHS-POP (Global Human Settlement Layer)](https://human-settlement.emergency.copernicus.eu/download.php?ds=pop)
dataset, clipped to a boundary polygon of your choice.

Each output grid cell corresponds exactly to one raster pixel and carries
that pixel's (rounded) population value.

## What it does

1. **Download (optional)** — automatically downloads the global GHS-POP
   raster for a chosen epoch (year), coordinate system, and resolution,
   and caches it locally so it isn't re-downloaded next time.
   You can also supply your own local raster(s) instead (or in addition).
2. **Mosaic** — merges multiple input rasters into one, if more than one is
   provided.
3. **Reproject boundary** — if your boundary polygon's coordinate system
   differs from the raster's, it's reprojected automatically first.
4. **Clip** — clips the raster to your boundary polygon (true polygon clip,
   not just a bounding box).
5. **Raster → Point** — converts each remaining pixel to a point carrying
   its value.
6. **Fishnet** — builds a polygon grid aligned exactly to the raster's
   pixel size and extent.
7. **Spatial Join** — transfers each pixel's value into its matching grid
   cell (one point per cell, guaranteed by the aligned grid).
8. **Round** — rounds each cell's population value using standard
   half-up rounding (< 0.5 rounds down, ≥ 0.5 rounds up).
9. **Remove zeros** — deletes cells whose rounded population is 0.
10. **Symbology** — renames the value field to something readable and
    applies a 5-class Natural Breaks graduated-color renderer to the
    output layer in the active map. Optionally also sets the map's
    coordinate system to match the output so cells aren't shown skewed.

## Requirements

- ArcGIS Pro (tested with the standard `arcgispy3` conda environment, which
  already includes `requests`)
- Internet access if using the auto-download option
- A boundary polygon feature (shapefile, feature class, etc.) to clip to

## Installation

1. Download `PopulationGridTool.pyt` from this repo.
2. In ArcGIS Pro, open the **Catalog** pane → right-click **Toolboxes** →
   **Add Toolbox** → select the downloaded `.pyt` file.
3. The tool **Raster to Population Grid** will appear inside it.

## Usage

Open the tool and fill in:

| Parameter | Description |
|---|---|
| Input Population Raster(s) | Optional local raster(s). Leave empty if using auto-download. |
| Auto-download GHS-POP raster | Check to download automatically from JRC. |
| GHS-POP Epoch (year) | Year to download (only used if auto-download is checked). |
| Coordinate System | `Mollweide (54009)` or `WGS84 (4326)`. |
| Resolution | Depends on the chosen coordinate system (100 m / 1000 m for Mollweide, 3 / 30 arcsec for WGS84). |
| Download Cache Folder | Where downloaded rasters are cached (optional; defaults to the ArcGIS scratch folder). |
| Boundary Feature | Polygon feature used to clip the raster. |
| Output Population Grid | Output feature class (a sensible default name is auto-suggested). |
| Population Field Name | Name of the population field in the output (default: `Population`). |
| Set Basemap Coordinate System to Match Output | If checked, sets the active map's CRS to match the output at the end. |

Click **Run**. Progress is shown step-by-step (9 steps) in the
Geoprocessing pane.

## Notes

- JRC occasionally updates the GHSL release version (currently `R2023A`
  in this tool). If downloads start failing with a 404 error, check the
  [GHSL download page](https://human-settlement.emergency.copernicus.eu/download.php?ds=pop)
  for the current release code and update the `GHSL_RELEASE` constant near
  the top of `PopulationGridTool.pyt`.
- Symbology and the basemap CRS step require an active ArcGIS Pro project
  with an open map; if run outside of one (e.g. standalone script), those
  two steps are skipped with a warning, but the grid itself is still built.

## License

MIT — see [LICENSE](LICENSE).

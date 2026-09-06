import arcpy
import os


class Toolbox(object):
    def __init__(self):
        self.label = "Population Grid Toolbox"
        self.alias = "popgrid"
        self.tools = [RasterToPopulationGrid]


class RasterToPopulationGrid(object):
    """
    Builds a population vector grid (cell size == raster pixel size) from
    GHS-POP data, clipped to a boundary polygon, with population values
    rounded, zero-population cells removed, and graduated-color symbology
    applied.

    The raster can either be:
      (a) downloaded automatically from JRC's GHSL open-data FTP
          (single global file per epoch/CRS/resolution combination), or
      (b) supplied manually as one or more local raster files.
    Both can be combined; anything given is mosaicked together first.

    Workflow:
        1. Mosaic         -> merge all input rasters into one (skipped if
                              only one raster is involved)
        2. Reproject clip -> if the boundary polygon's CRS differs from the
                              raster's, reproject it to match first
        3. Clip           -> clip the raster to the boundary polygon
        4. Raster to Point -> one point per remaining pixel, carrying its value
        5. Create Fishnet -> polygon grid aligned exactly to the clipped raster
        6. Spatial Join   -> transfers each point's value into its matching
                              cell (NoData pixels produce no point, so cells
                              outside the clip boundary are automatically
                              dropped)
        7. Round          -> rounds each cell's population value (half-up)
        8. Remove zeros   -> deletes cells whose rounded population is 0
        9. Field rename + symbology -> renames the value field to a readable
                              name, applies a 5-class Natural Breaks
                              graduated-color renderer, and (optionally)
                              sets the active map's coordinate system to
                              match the raster's CRS so cells are not
                              displayed skewed
    """

    # NOTE: JRC occasionally bumps the GHSL release version (currently
    # R2023A). If downloads start failing with a 404, check
    # https://human-settlement.emergency.copernicus.eu/download.php?ds=pop
    # for the current release code and update GHSL_RELEASE below.
    GHSL_RELEASE = "R2023A"
    GHSL_EPOCHS = [
        "1975", "1980", "1985", "1990", "1995", "2000", "2005",
        "2010", "2015", "2020", "2025", "2030",
    ]

    # Display label -> (EPSG code, short name used in output filenames)
    CRS_OPTIONS = {
        "Mollweide (54009)": ("54009", "Mollweide"),
        "WGS84 (4326)": ("4326", "WGS84"),
    }

    # EPSG code -> {display label -> resolution code used in GHSL file names}
    RESOLUTION_OPTIONS = {
        "54009": {
            "100 m": "100",
            "1000 m (~1 km)": "1000",
        },
        "4326": {
            "3 arcsec (~100 m)": "3ss",
            "30 arcsec (~1 km)": "30ss",
        },
    }

    def __init__(self):
        self.label = "Raster to Population Grid"
        self.description = (
            "Downloads (optional) and/or merges GHS-POP raster(s), clips "
            "them to a boundary, converts the result into a vector grid "
            "where each cell holds the rounded population value of the "
            "corresponding pixel (zero-population cells removed), and "
            "applies a 5-class Natural Breaks renderer."
        )
        self.canRunInBackground = False
        self._last_suggested_name = None

    def getParameterInfo(self):
        in_rasters = arcpy.Parameter(
            displayName="Input Population Raster(s) (optional if downloading)",
            name="in_rasters",
            datatype="GPRasterLayer",
            parameterType="Optional",
            direction="Input",
            multiValue=True,
        )

        download_ghsl = arcpy.Parameter(
            displayName="Auto-download GHS-POP raster",
            name="download_ghsl",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
        )
        download_ghsl.value = False

        epoch = arcpy.Parameter(
            displayName="GHS-POP Epoch (year)",
            name="epoch",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
        )
        epoch.filter.type = "ValueList"
        epoch.filter.list = self.GHSL_EPOCHS
        epoch.value = "2020"

        coord_system = arcpy.Parameter(
            displayName="Coordinate System",
            name="coord_system",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
        )
        coord_system.filter.type = "ValueList"
        coord_system.filter.list = list(self.CRS_OPTIONS.keys())
        coord_system.value = "Mollweide (54009)"

        resolution = arcpy.Parameter(
            displayName="Resolution",
            name="resolution",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
        )
        resolution.filter.type = "ValueList"
        resolution.filter.list = list(
            self.RESOLUTION_OPTIONS[self.CRS_OPTIONS["Mollweide (54009)"][0]].keys()
        )
        resolution.value = "1000 m (~1 km)"

        download_folder = arcpy.Parameter(
            displayName="Download Cache Folder",
            name="download_folder",
            datatype="DEFolder",
            parameterType="Optional",
            direction="Input",
        )

        clip_features = arcpy.Parameter(
            displayName="Boundary Feature (clip extent)",
            name="clip_features",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input",
        )
        clip_features.filter.list = ["Polygon"]

        out_fc = arcpy.Parameter(
            displayName="Output Population Grid",
            name="out_fc",
            datatype="DEFeatureClass",
            parameterType="Required",
            direction="Output",
        )

        pop_field_name = arcpy.Parameter(
            displayName="Population Field Name",
            name="pop_field_name",
            datatype="GPString",
            parameterType="Optional",
            direction="Input",
        )
        pop_field_name.value = "Population"

        set_basemap_crs = arcpy.Parameter(
            displayName="Set Basemap Coordinate System to Match Output",
            name="set_basemap_crs",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input",
        )
        set_basemap_crs.value = True

        return [
            in_rasters, download_ghsl, epoch, coord_system, resolution,
            download_folder, clip_features, out_fc, pop_field_name,
            set_basemap_crs,
        ]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        download_ghsl = parameters[1].value
        parameters[2].enabled = bool(download_ghsl)   # epoch
        parameters[3].enabled = bool(download_ghsl)   # coord_system
        parameters[4].enabled = bool(download_ghsl)   # resolution
        parameters[5].enabled = bool(download_ghsl)   # download_folder

        # Keep the resolution list in sync with the chosen coordinate system.
        # Always rebuild and reassign explicitly (rather than comparing to
        # the existing list) since ArcGIS Pro does not reliably detect
        # in-place list mutations on parameter.filter.list.
        coord_system_param = parameters[3]
        resolution_param = parameters[4]
        crs_code = None
        if coord_system_param.value:
            crs_code = self.CRS_OPTIONS.get(coord_system_param.value, (None, None))[0]
            valid_labels = list(self.RESOLUTION_OPTIONS.get(crs_code, {}).keys())
            current_value = resolution_param.value
            resolution_param.filter.list = valid_labels
            if current_value not in valid_labels:
                resolution_param.value = valid_labels[0] if valid_labels else None

        # Suggest a default output name based on epoch/resolution/CRS,
        # without clobbering a workspace or custom name the user already
        # picked.
        out_fc_param = parameters[7]
        epoch_val = parameters[2].valueAsText
        res_label = parameters[4].valueAsText
        if download_ghsl and epoch_val and crs_code and res_label:
            res_code = self.RESOLUTION_OPTIONS.get(crs_code, {}).get(res_label, "")
            suggested = "PopGrid_{0}_{1}".format(epoch_val, res_code)
        else:
            suggested = "PopulationGrid"

        current_value = out_fc_param.valueAsText
        if not current_value:
            out_fc_param.value = suggested
            self._last_suggested_name = suggested
        else:
            current_dir = os.path.dirname(current_value)
            current_base = os.path.basename(current_value)
            if current_base == self._last_suggested_name:
                new_value = os.path.join(current_dir, suggested) if current_dir else suggested
                out_fc_param.value = new_value
                self._last_suggested_name = suggested
        return

    def updateMessages(self, parameters):
        in_rasters = parameters[0].values
        download_ghsl = parameters[1].value
        if not in_rasters and not download_ghsl:
            parameters[0].setErrorMessage(
                "Provide at least one raster, or enable auto-download."
            )
        return

    def _download_ghs_pop(self, epoch, crs_code, res_label, download_folder, messages):
        import requests
        import zipfile

        if not os.path.isdir(download_folder):
            os.makedirs(download_folder)

        res_code = self.RESOLUTION_OPTIONS[crs_code][res_label]

        base_name = "GHS_POP_E{epoch}_GLOBE_{release}_{crs}_{res}_V1_0".format(
            epoch=epoch, release=self.GHSL_RELEASE, crs=crs_code, res=res_code
        )
        url = (
            "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
            "GHS_POP_GLOBE_{release}/GHS_POP_E{epoch}_GLOBE_{release}_{crs}_{res}/"
            "V1-0/{name}.zip"
        ).format(release=self.GHSL_RELEASE, epoch=epoch, crs=crs_code, res=res_code, name=base_name)

        zip_path = os.path.join(download_folder, base_name + ".zip")
        tif_path = os.path.join(download_folder, base_name + ".tif")

        if os.path.exists(tif_path):
            messages.addMessage("Cached raster found, skipping download: {}".format(tif_path))
            return tif_path

        if not os.path.exists(zip_path):
            messages.addMessage("Downloading: {}".format(url))
            with requests.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(zip_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
            messages.addMessage("Download complete.")
        else:
            messages.addMessage("Cached zip found, skipping download.")

        messages.addMessage("Extracting archive...")
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(download_folder)

        if not os.path.exists(tif_path):
            for fn in os.listdir(download_folder):
                if fn.lower().endswith(".tif") and base_name in fn:
                    tif_path = os.path.join(download_folder, fn)
                    break

        if not os.path.exists(tif_path):
            raise RuntimeError(
                "Downloaded archive did not contain the expected raster '{}'. "
                "Check the extracted files in {}".format(base_name, download_folder)
            )

        return tif_path

    def _apply_symbology(self, out_fc, pop_field, sr, set_basemap_crs, messages):
        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
        except Exception:
            messages.addWarningMessage(
                "No active ArcGIS Pro project found; skipping symbology and map CRS steps."
            )
            return

        active_map = aprx.activeMap
        if active_map is None:
            messages.addWarningMessage("No active map found; skipping symbology and map CRS steps.")
            return

        # Find the output layer in the active map, or add it if not present
        lyr = None
        for l in active_map.listLayers():
            try:
                if l.supports("DATASOURCE") and os.path.normpath(l.dataSource) == os.path.normpath(out_fc):
                    lyr = l
                    break
            except Exception:
                continue
        if lyr is None:
            lyr = active_map.addDataFromPath(out_fc)

        try:
            sym = lyr.symbology
            sym.updateRenderer("GraduatedColorsRenderer")
            sym.renderer.classificationField = pop_field
            sym.renderer.breakCount = 5
            sym.renderer.classificationMethod = "NaturalBreaks"
            try:
                ramp = aprx.listColorRamps("Yellow-Orange-Red (Continuous)")[0]
                sym.renderer.colorRamp = ramp
            except Exception:
                pass
            lyr.symbology = sym
            messages.addMessage(
                "Symbology applied: graduated colors, Natural Breaks, 5 classes."
            )
        except Exception as e:
            messages.addWarningMessage("Could not apply symbology: {}".format(e))

        if set_basemap_crs:
            try:
                active_map.spatialReference = sr
                messages.addMessage(
                    "Map coordinate system set to match output ({}).".format(sr.name)
                )
            except Exception as e:
                messages.addWarningMessage("Could not set map coordinate system: {}".format(e))
        else:
            messages.addMessage("Skipped changing the map's coordinate system (unchecked).")

    def execute(self, parameters, messages):
        raster_values = parameters[0].values
        manual_raster_paths = [str(r) for r in raster_values] if raster_values else []
        download_ghsl = parameters[1].value
        epoch = parameters[2].valueAsText
        coord_system_label = parameters[3].valueAsText
        res_label = parameters[4].valueAsText
        download_folder = parameters[5].valueAsText or os.path.join(
            arcpy.env.scratchFolder, "GHSL_cache"
        )
        clip_features = parameters[6].valueAsText
        out_fc = parameters[7].valueAsText
        pop_field = parameters[8].valueAsText or "Population"
        set_basemap_crs = parameters[9].value

        arcpy.env.overwriteOutput = True
        scratch_gdb = arcpy.env.scratchGDB

        total_steps = 9
        arcpy.SetProgressor("step", "Building population grid...", 0, total_steps, 1)

        raster_paths = []
        if download_ghsl:
            crs_code = self.CRS_OPTIONS[coord_system_label][0]
            downloaded_tif = self._download_ghs_pop(
                epoch, crs_code, res_label, download_folder, messages
            )
            raster_paths.append(downloaded_tif)
        raster_paths.extend(manual_raster_paths)

        if not raster_paths:
            raise RuntimeError("No input raster available: provide one or enable auto-download.")

        first_desc = arcpy.Describe(raster_paths[0])
        sr = first_desc.spatialReference
        pixel_type = first_desc.pixelType
        band_count = first_desc.bandCount
        arcpy.env.outputCoordinateSystem = sr

        # --- Step 1: Mosaic (only if more than one raster) ---------------
        arcpy.SetProgressorLabel("Step 1/{}: mosaicking rasters...".format(total_steps))
        if len(raster_paths) > 1:
            mosaic_name = "mosaic_raster"
            arcpy.management.MosaicToNewRaster(
                input_rasters=";".join(raster_paths),
                output_location=scratch_gdb,
                raster_dataset_name_with_extension=mosaic_name,
                coordinate_system_for_the_raster=sr,
                pixel_type=pixel_type,
                number_of_bands=band_count,
                mosaic_method="FIRST",
            )
            source_raster = os.path.join(scratch_gdb, mosaic_name)
            messages.addMessage(
                "Step 1/{} done: {} rasters mosaicked.".format(total_steps, len(raster_paths))
            )
        else:
            source_raster = raster_paths[0]
            messages.addMessage("Step 1/{} skipped: single raster provided.".format(total_steps))
        arcpy.SetProgressorPosition(1)

        # --- Step 2: Reproject boundary polygon if its CRS differs -------
        arcpy.SetProgressorLabel("Step 2/{}: checking boundary CRS...".format(total_steps))
        clip_desc = arcpy.Describe(clip_features)
        clip_sr = clip_desc.spatialReference
        projected_clip = None
        if clip_sr.factoryCode and sr.factoryCode and clip_sr.factoryCode != sr.factoryCode:
            projected_clip = os.path.join(scratch_gdb, "clip_features_proj")
            arcpy.management.Project(clip_features, projected_clip, sr)
            clip_features_for_clip = projected_clip
            messages.addMessage(
                "Step 2/{} done: boundary feature reprojected to match raster CRS.".format(total_steps)
            )
        else:
            clip_features_for_clip = clip_features
            messages.addMessage(
                "Step 2/{} skipped: boundary already matches raster CRS.".format(total_steps)
            )
        arcpy.SetProgressorPosition(2)

        # --- Step 3: Clip to boundary polygon -----------------------------
        arcpy.SetProgressorLabel("Step 3/{}: clipping raster...".format(total_steps))
        clip_extent = arcpy.Describe(clip_features_for_clip).extent
        rectangle = "{} {} {} {}".format(
            clip_extent.XMin, clip_extent.YMin, clip_extent.XMax, clip_extent.YMax
        )
        clipped_raster = os.path.join(scratch_gdb, "clipped_raster")
        arcpy.management.Clip(
            in_raster=source_raster,
            rectangle=rectangle,
            out_raster=clipped_raster,
            in_template_dataset=clip_features_for_clip,
            nodata_value="",
            clipping_geometry="ClippingGeometry",
            maintain_clipping_extent="NO_MAINTAIN_EXTENT",
        )
        messages.addMessage("Step 3/{} done: raster clipped to boundary.".format(total_steps))
        arcpy.SetProgressorPosition(3)

        desc = arcpy.Describe(clipped_raster)
        cell_width = desc.meanCellWidth
        cell_height = desc.meanCellHeight
        extent = desc.extent

        # --- Step 4: Raster to Point --------------------------------------
        arcpy.SetProgressorLabel("Step 4/{}: converting raster to points...".format(total_steps))
        raster_points = os.path.join(scratch_gdb, "raster_pts")
        arcpy.conversion.RasterToPoint(clipped_raster, raster_points, "VALUE")
        messages.addMessage("Step 4/{} done: raster converted to points.".format(total_steps))
        arcpy.SetProgressorPosition(4)

        # --- Step 5: Create Fishnet aligned to the clipped raster ---------
        arcpy.SetProgressorLabel("Step 5/{}: building fishnet grid...".format(total_steps))
        fishnet_fc = os.path.join(scratch_gdb, "fishnet_grid")
        origin_coord = "{} {}".format(extent.XMin, extent.YMin)
        y_axis_coord = "{} {}".format(extent.XMin, extent.YMin + 10)
        corner_coord = "{} {}".format(extent.XMax, extent.YMax)

        arcpy.management.CreateFishnet(
            out_feature_class=fishnet_fc,
            origin_coord=origin_coord,
            y_axis_coord=y_axis_coord,
            cell_width=cell_width,
            cell_height=cell_height,
            number_rows=0,
            number_columns=0,
            corner_coord=corner_coord,
            labels="NO_LABELS",
            template=clipped_raster,
            geometry_type="POLYGON",
        )
        messages.addMessage(
            "Step 5/{} done: fishnet grid created (aligned to raster).".format(total_steps)
        )
        arcpy.SetProgressorPosition(5)

        # --- Step 6: Spatial Join ------------------------------------------
        arcpy.SetProgressorLabel("Step 6/{}: joining values to grid...".format(total_steps))
        arcpy.analysis.SpatialJoin(
            target_features=fishnet_fc,
            join_features=raster_points,
            out_feature_class=out_fc,
            join_operation="JOIN_ONE_TO_ONE",
            join_type="KEEP_COMMON",
            match_option="CONTAINS",
        )
        messages.addMessage("Step 6/{} done: spatial join complete.".format(total_steps))
        arcpy.SetProgressorPosition(6)

        # --- Step 7: round population values (half-up) --------------------
        arcpy.SetProgressorLabel("Step 7/{}: rounding population values...".format(total_steps))
        round_code_block = (
            "import math\n"
            "def round_half_up(x):\n"
            "    if x is None:\n"
            "        return x\n"
            "    return int(math.floor(x + 0.5))"
        )
        arcpy.management.CalculateField(
            out_fc, "grid_code", "round_half_up(!grid_code!)", "PYTHON3", round_code_block
        )
        messages.addMessage("Step 7/{} done: population values rounded.".format(total_steps))
        arcpy.SetProgressorPosition(7)

        # --- Step 8: remove zero-population cells (post-rounding) ---------
        arcpy.SetProgressorLabel("Step 8/{}: removing zero-population cells...".format(total_steps))
        delim_field = arcpy.AddFieldDelimiters(out_fc, "grid_code")
        lyr = arcpy.management.MakeFeatureLayer(out_fc, "out_lyr")
        arcpy.management.SelectLayerByAttribute(
            lyr, "NEW_SELECTION", "{} = 0".format(delim_field)
        )
        removed = int(arcpy.management.GetCount(lyr)[0])
        arcpy.management.DeleteFeatures(lyr)
        messages.addMessage(
            "Step 8/{} done: {} zero-population cells removed.".format(total_steps, removed)
        )
        arcpy.SetProgressorPosition(8)

        # --- Step 9: rename field + symbology + map CRS --------------------
        arcpy.SetProgressorLabel("Step 9/{}: finishing up...".format(total_steps))
        if pop_field != "grid_code":
            arcpy.management.AddField(out_fc, pop_field, "LONG")
            arcpy.management.CalculateField(out_fc, pop_field, "!grid_code!", "PYTHON3")
            arcpy.management.DeleteField(out_fc, "grid_code")
        messages.addMessage("Field renamed to '{}'.".format(pop_field))

        # --- Cleanup intermediate data -------------------------------------
        for tmp in [raster_points, fishnet_fc]:
            arcpy.management.Delete(tmp)
        if len(raster_paths) > 1:
            arcpy.management.Delete(source_raster)
        arcpy.management.Delete(clipped_raster)
        if projected_clip:
            arcpy.management.Delete(projected_clip)

        self._apply_symbology(out_fc, pop_field, sr, set_basemap_crs, messages)
        arcpy.SetProgressorPosition(9)
        messages.addMessage("Step 9/{} done.".format(total_steps))

        messages.addMessage("Finished. Output saved to: {}".format(out_fc))
        return

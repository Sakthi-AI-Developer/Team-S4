import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from processing.change_detection import calculate_change
from processing.ndbi import calculate_ndbi
from processing.ndvi import calculate_ndvi
from processing.ndwi import calculate_ndwi
from processing.preprocessing import DatasetError, RasterBand, build_profile, load_dataset, validate_alignment
from processing.landcover import LandCoverClassifier


def make_band(code, data, transform=None, crs="EPSG:32644"):
    array = np.asarray(data, dtype=np.float32)
    return RasterBand(
        code=code,
        data=array,
        valid=np.isfinite(array),
        profile={
            "driver": "GTiff",
            "height": array.shape[0],
            "width": array.shape[1],
            "count": 1,
            "dtype": "float32",
            "crs": crs,
            "transform": transform or from_origin(500000, 3000000, 10, 10),
        },
        crs=crs,
        transform=transform or from_origin(500000, 3000000, 10, 10),
        width=array.shape[1],
        height=array.shape[0],
        resolution=(10, 10),
    )


def test_ndvi_formula_and_summary():
    result = calculate_ndvi(np.array([[0.2, 0.1]]), np.array([[0.6, 0.3]]))
    np.testing.assert_allclose(result["raster"], [[0.5, 0.5]], atol=1e-6)
    assert result["statistics"]["mean"] == pytest.approx(0.5)
    assert result["statistics"]["valid_pixels"] == 2


def test_ndwi_mcfeeters_formula():
    result = calculate_ndwi(np.array([[0.6]]), np.array([[0.2]]))
    assert result["raster"][0, 0] == pytest.approx(0.5)
    assert "McFeeters" in result["formulation"]


def test_ndbi_formula():
    result = calculate_ndbi(np.array([[0.2]]), np.array([[0.6]]))
    assert result["raster"][0, 0] == pytest.approx(0.5)
    assert result["built_up_indicator_percentage"] == 100


def test_zero_denominator_is_invalid_not_infinite():
    result = calculate_ndvi(np.array([[0.0, 0.1]]), np.array([[0.0, 0.3]]))
    assert np.isnan(result["raster"][0, 0])
    assert np.isfinite(result["raster"][0, 1])
    assert result["statistics"]["valid_pixels"] == 1


def test_missing_band_has_clear_error(tmp_path):
    path = tmp_path / "B04.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype="float32",
        crs="EPSG:32644",
        transform=from_origin(0, 20, 10, 10),
    ) as output:
        output.write(np.ones((2, 2), dtype=np.float32), 1)
    dataset = load_dataset(tmp_path)
    with pytest.raises(DatasetError, match="B08"):
        dataset.require(("B04", "B08"))


def test_dataset_loader_keeps_corrupt_raster_errors(tmp_path):
    (tmp_path / "B04.tif").write_bytes(b"not a GeoTIFF")
    dataset = load_dataset(tmp_path, required=("B04",))
    assert "B04" in dataset.missing_bands
    assert dataset.errors
    with pytest.raises(DatasetError, match="B04"):
        dataset.require(("B04",))


def test_alignment_requires_known_crs():
    first = make_band("B04", [[0.2]], crs=None)
    second = make_band("B08", [[0.6]], crs=None)
    with pytest.raises(DatasetError, match="no CRS"):
        validate_alignment([first, second])


def test_landcover_excludes_pixels_with_undefined_indices():
    bands = {
        "B03": make_band("B03", [[0.0]]),
        "B04": make_band("B04", [[0.0]]),
        "B08": make_band("B08", [[0.0]]),
        "B11": make_band("B11", [[0.0]]),
    }
    result = LandCoverClassifier().classify(bands)
    assert not result["valid_mask"][0, 0]
    assert result["raster"][0, 0] == 0


def test_output_profile_preserves_spatial_metadata(tmp_path):
    transform = from_origin(1234, 5678, 20, 20)
    band = make_band("B04", [[1, 2], [3, 4]], transform)
    output_path = tmp_path / "preserved.tif"
    profile = build_profile(band)
    with rasterio.open(output_path, "w", **profile) as output:
        output.write(band.data, 1)
    with rasterio.open(output_path) as output:
        assert output.crs.to_string() == band.crs
        assert output.transform == transform
        assert (output.width, output.height) == (2, 2)


def test_change_detection_returns_difference_and_percentages():
    current_red = make_band("B04", [[0.2, 0.1]])
    current_nir = make_band("B08", [[0.6, 0.3]])
    historical_red = make_band("B04", [[0.4, 0.2]])
    historical_nir = make_band("B08", [[0.4, 0.2]])
    result = calculate_change(current_red, current_nir, historical_red, historical_nir)
    np.testing.assert_allclose(result["raster"], [[0.5, 0.5]], atol=1e-6)
    assert result["positive_change_percentage"] == 100
    assert result["negative_change_percentage"] == 0
    assert result["current_mean"] == pytest.approx(0.5)
    assert result["historical_mean"] == pytest.approx(0.0)


def test_change_detection_rejects_misaligned_rasters():
    current_red = make_band("B04", [[0.2]], from_origin(0, 10, 10, 10))
    current_nir = make_band("B08", [[0.6]], from_origin(0, 10, 10, 10))
    historic_red = make_band("B04", [[0.4]], from_origin(10, 10, 10, 10))
    historic_nir = make_band("B08", [[0.4]], from_origin(10, 10, 10, 10))
    with pytest.raises(DatasetError, match="geotransforms"):
        calculate_change(current_red, current_nir, historic_red, historic_nir)

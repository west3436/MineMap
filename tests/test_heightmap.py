import numpy as np

from minemap.pipeline.heightmap import bake, deseam
from minemap.project import ScaleSettings


def test_bake_clamps():
    sc = ScaleSettings(deseam=False)
    elev = np.array([[-9000.0, 0.0, 3776.0, 90000.0]], np.float32)
    Y = bake(elev, sc)
    assert Y.dtype == np.float32
    assert Y[0, 0] == sc.y_min and Y[0, 3] == sc.y_max
    assert Y[0, 1] == sc.water_level
    assert abs(Y[0, 2] - (62 + 3776 * 0.04)) < 1e-3


def test_deseam_preserves_flat():
    flat = np.full((10, 6), 80.0, np.float32)
    assert np.allclose(deseam(flat), flat)


def test_deseam_softens_step():
    Y = np.full((10, 4), 60.0, np.float32)
    Y[5:] = 90.0
    out = deseam(Y)
    assert out[4, 0] > 60 and out[5, 0] < 90        # step became a ramp
    assert out.max() <= 90 and out.min() >= 60
    assert np.allclose(out[:, 0], out[:, 3])         # no east-west change

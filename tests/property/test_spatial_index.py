"""Proofs for the spatial index: location-relevant retrieval by geohash prefix."""

import pytest

from knitweb.fabric import spatial
from knitweb.fabric.spatial_index import SpatialIndex
from knitweb.fabric.web import Web


@pytest.mark.property
def test_near_returns_only_nearby_targets():
    idx = SpatialIndex()
    idx.add(spatial.bind(52.3702, 4.8952, "ams-a", precision=9))   # Amsterdam
    idx.add(spatial.bind(52.3705, 4.8955, "ams-b", precision=9))   # ~40m away
    idx.add(spatial.bind(48.8566, 2.3522, "paris", precision=9))   # far

    here = spatial.geohash(52.3702, 4.8952, 9)
    near = idx.near(here, precision=5)
    assert "ams-a" in near and "ams-b" in near
    assert "paris" not in near


@pytest.mark.property
def test_near_filters_by_altitude_band():
    idx = SpatialIndex()
    idx.add(spatial.bind(52.3702, 4.8952, "ground", precision=9, altitude_m=1.0))   # band 0
    idx.add(spatial.bind(52.3702, 4.8952, "tenth-floor", precision=9, altitude_m=30.0))  # band 10
    here = spatial.geohash(52.3702, 4.8952, 9)
    assert idx.near(here, precision=7, alt_band=0) == ["ground"]
    assert idx.near(here, precision=7, alt_band=10) == ["tenth-floor"]


@pytest.mark.property
def test_from_web_reconstructs_index():
    web = Web()
    spatial.bind(52.3702, 4.8952, "t1", precision=9).weave(web)
    spatial.bind(48.8566, 2.3522, "t2", precision=9).weave(web)
    web.weave({"kind": "knowledge", "title": "not an anchor"})  # ignored

    idx = SpatialIndex.from_web(web)
    assert len(idx) == 2
    here = spatial.geohash(52.3702, 4.8952, 9)
    assert idx.near(here, precision=6) == ["t1"]


@pytest.mark.property
def test_near_is_deterministic_and_deduped():
    idx = SpatialIndex()
    # same target anchored twice -> appears once
    idx.add(spatial.bind(52.3702, 4.8952, "dup", precision=9))
    idx.add(spatial.bind(52.3702, 4.8952, "dup", precision=9))
    here = spatial.geohash(52.3702, 4.8952, 9)
    assert idx.near(here, precision=7) == ["dup"]

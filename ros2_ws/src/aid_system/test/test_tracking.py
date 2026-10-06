from aid_system.tracking import TrackingSearch, angular_difference


def test_tracking_search_recenters_toward_stronger_clockwise_signal():
    search = TrackingSearch(3.0, 1.0, 0.5, 1.0)

    assert search.start(100.0) == 103.0
    search.target_reached(10.0)
    search.add_sample(12.0)
    assert search.advance(10.9) is None
    assert search.advance(11.0) == 97.0

    search.target_reached(12.0)
    search.add_sample(8.0)
    assert search.advance(13.0) == 104.0
    assert search.center_deg == 101.0


def test_tracking_search_respects_deadband_and_wraps_angles():
    search = TrackingSearch(3.0, 1.0, 1.0, 0.5)

    assert search.start(359.0) == 2.0
    search.target_reached(0.0)
    search.add_sample(10.0)
    assert search.advance(0.5) == 356.0
    search.target_reached(1.0)
    search.add_sample(9.5)
    assert search.advance(1.5) == 2.0
    assert search.center_deg == 359.0
    assert angular_difference(2.0, 359.0) == 3.0
from aid_system.dual_band import DualBandScheduler


def test_one_rotation_then_five_second_band_switch():
    scheduler = DualBandScheduler(switch_seconds=5.0)
    scheduler.start_scan("2.4", 20.0)

    assert scheduler.observe_angle(120.0, 0.0) is None
    assert scheduler.observe_angle(220.0, 1.0) is None
    assert scheduler.observe_angle(320.0, 2.0) is None
    assert scheduler.observe_angle(19.5, 3.0) == "5.8"
    assert scheduler.phase == "switching"

    assert not scheduler.ready_to_resume(7.9, "5.8", 19.5)
    assert not scheduler.ready_to_resume(8.0, "2.4", 19.5)
    assert scheduler.ready_to_resume(8.0, "5.8", 19.5)
    assert scheduler.phase == "scanning"
    assert scheduler.active_band == "5.8"


def test_scheduler_alternates_back_to_2_4():
    scheduler = DualBandScheduler(switch_seconds=5.0)
    scheduler.start_scan("5.8", 0.0)
    for angle in (90.0, 180.0, 270.0):
        assert scheduler.observe_angle(angle, angle / 90.0) is None
    assert scheduler.observe_angle(359.5, 4.0) == "2.4"


def test_initial_switch_also_waits_five_seconds_and_for_ready_band():
    scheduler = DualBandScheduler(switch_seconds=5.0)
    scheduler.start_switch("2.4", 10.0, 42.0)

    assert not scheduler.ready_to_resume(15.0, "5.8", 42.0)
    assert scheduler.ready_to_resume(15.0, "2.4", 42.0)
    assert scheduler.phase == "scanning"
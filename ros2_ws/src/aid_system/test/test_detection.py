from aid_system.detection import BaselineModel


def test_baseline_rejects_quiet_and_detects_broadband_outlier():
    quiet = {
        "band": "2.4ghz",
        "start_mhz": 2400.0,
        "step_mhz": 1.0,
        "amplitudes_dbm": [-95.0] * 84,
    }
    model = BaselineModel()
    for offset in (0.0, 0.5, -0.5, 0.0, 0.25):
        model.update({**quiet, "amplitudes_dbm": [-95.0 + offset] * 84})

    quiet_result = model.analyze(quiet, 5.0, 6.0, 4.0, 1.5)
    signal = {**quiet, "amplitudes_dbm": [-95.0] * 40 + [-70.0] * 8 + [-95.0] * 36}
    signal_result = model.analyze(signal, 5.0, 6.0, 4.0, 1.5)

    assert not quiet_result["viable"]
    assert signal_result["viable"]
    assert 2439.0 <= signal_result["frequency_mhz"] <= 2448.0


def test_excluded_range_masks_own_hotspot_but_keeps_other_outliers():
    quiet = {
        "band": "2.4ghz",
        "start_mhz": 2400.0,
        "step_mhz": 1.0,
        "amplitudes_dbm": [-95.0] * 84,
    }
    model = BaselineModel()
    for offset in (0.0, 0.5, -0.5, 0.0, 0.25):
        model.update({**quiet, "amplitudes_dbm": [-95.0 + offset] * 84})

    channel_1 = [(2402.0, 2422.0)]
    # Spike at 2412 MHz (bin 12) is inside excluded channel 1.
    hotspot = {**quiet, "amplitudes_dbm": [-95.0] * 10 + [-60.0] * 5 + [-95.0] * 69}
    hotspot_result = model.analyze(hotspot, 5.0, 6.0, 4.0, 1.5, channel_1)
    assert not hotspot_result["viable"]

    # Spike at 2440 MHz (bin 40) is outside exclusion and must still be detected.
    drone = {**quiet, "amplitudes_dbm": [-95.0] * 38 + [-60.0] * 5 + [-95.0] * 41}
    drone_result = model.analyze(drone, 5.0, 6.0, 4.0, 1.5, channel_1)
    assert drone_result["viable"]
    assert 2435.0 <= drone_result["frequency_mhz"] <= 2445.0


def test_tracking_measures_target_frequency_instead_of_stronger_outlier():
    quiet = {
        "band": "2.4ghz",
        "start_mhz": 2400.0,
        "step_mhz": 1.0,
        "amplitudes_dbm": [-95.0] * 84,
    }
    model = BaselineModel()
    for offset in (0.0, 0.5, -0.5, 0.0, 0.25):
        model.update({**quiet, "amplitudes_dbm": [-95.0 + offset] * 84})

    sweep = {
        **quiet,
        "amplitudes_dbm": [-95.0] * 38
        + [-70.0] * 5
        + [-95.0] * 17
        + [-55.0] * 5
        + [-95.0] * 19,
    }
    result = model.analyze(
        sweep,
        5.0,
        6.0,
        4.0,
        1.5,
        target_frequency_mhz=2440.0,
    )

    assert result["viable"]
    assert 2439.0 <= result["frequency_mhz"] <= 2441.0
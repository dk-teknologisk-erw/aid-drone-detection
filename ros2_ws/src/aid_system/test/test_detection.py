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
        "band": "custom",
        "start_mhz": 5150.0,
        "step_mhz": 1.0,
        "amplitudes_dbm": [-95.0] * 100,
    }
    model = BaselineModel()
    for offset in (0.0, 0.5, -0.5, 0.0, 0.25):
        model.update({**quiet, "amplitudes_dbm": [-95.0 + offset] * 100})

    channel_36 = [(5170.0, 5190.0)]
    # Spike at 5180 MHz (bin 30) — inside excluded channel 36.
    hotspot = {**quiet, "amplitudes_dbm": [-95.0] * 28 + [-60.0] * 5 + [-95.0] * 67}
    hotspot_result = model.analyze(hotspot, 5.0, 6.0, 4.0, 1.5, channel_36)
    assert not hotspot_result["viable"]

    # Spike at 5230 MHz (bin 80) — outside exclusion, must still be detected.
    drone = {**quiet, "amplitudes_dbm": [-95.0] * 78 + [-60.0] * 5 + [-95.0] * 17}
    drone_result = model.analyze(drone, 5.0, 6.0, 4.0, 1.5, channel_36)
    assert drone_result["viable"]
    assert 5225.0 <= drone_result["frequency_mhz"] <= 5235.0
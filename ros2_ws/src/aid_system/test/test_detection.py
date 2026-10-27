import pytest

from aid_system.detection import (
    BaselineModel, CandidateHistory, analyze_without_baseline, live_maximum,
)


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


def signal_sweep(frequency=5780, level=-65.0):
    values = [-95.0] * 126
    index = frequency - 5725
    values[index - 3:index + 4] = [level] * 7
    return {
        "band": "5.8ghz", "start_mhz": 5725.0, "step_mhz": 1.0,
        "amplitudes_dbm": values,
    }


def test_no_baseline_detects_58ghz_without_calibration():
    result = analyze_without_baseline(signal_sweep(), 5.0, 6.0)
    assert result["viable"]
    assert 5777 <= result["frequency_mhz"] <= 5783
    assert result["snr_db"] == pytest.approx(30)
    assert "baseline_samples" not in result


def test_no_baseline_rejects_flat_noise_but_always_reports_raw_maximum():
    sweep = signal_sweep(level=-95.0)
    assert not analyze_without_baseline(sweep, 5.0, 6.0)["viable"]
    assert live_maximum(sweep) == {"frequency_mhz": 5725.0, "amplitude_dbm": -95.0}
    sweep["amplitudes_dbm"][87] = -60.0
    assert live_maximum(sweep) == {"frequency_mhz": 5812.0, "amplitude_dbm": -60.0}


def test_no_baseline_respects_masks_and_target_window():
    sweep = signal_sweep()
    sweep["amplitudes_dbm"][90:97] = [-40.0] * 7
    result = analyze_without_baseline(sweep, 5, 6, target_frequency_mhz=5780)
    assert result["viable"]
    assert abs(result["frequency_mhz"] - 5780) <= 10
    masked = analyze_without_baseline(sweep, 5, 6, [(5725, 5850)])
    assert masked["reason"] == "all_bins_excluded"
    outside = analyze_without_baseline(sweep, 5, 6, target_frequency_mhz=2440)
    assert outside["reason"] == "target_outside_scan"


def test_tracking_window_follows_frequency_change_without_chasing_distant_peak():
    sweep = signal_sweep(frequency=5786)
    sweep["amplitudes_dbm"][95:102] = [-40.0] * 7
    result = analyze_without_baseline(sweep, 5, 4, target_frequency_mhz=5780)
    assert result["viable"]
    assert 5783 <= result["frequency_mhz"] <= 5790
    baseline = BaselineModel()
    for _ in range(5):
        baseline.update(signal_sweep(level=-95.0))
    result = baseline.analyze(sweep, 5, 4, 3, 1.5,
                              target_frequency_mhz=5780, target_window_mhz=10)
    assert result["viable"]
    assert 5783 <= result["frequency_mhz"] <= 5790


def test_tracking_retention_threshold_is_lower_than_acquisition():
    sweep = signal_sweep(level=-90.0)
    assert not analyze_without_baseline(sweep, 5, 6)["viable"]
    assert analyze_without_baseline(sweep, 5, 4, target_frequency_mhz=5780)["viable"]


@pytest.mark.parametrize("invalid", [[], [float("nan")], [float("inf")]])
def test_invalid_sweeps_are_rejected(invalid):
    sweep = {**signal_sweep(), "amplitudes_dbm": invalid}
    with pytest.raises(ValueError):
        live_maximum(sweep)
    with pytest.raises(ValueError):
        analyze_without_baseline(sweep, 5, 6)


def test_candidates_require_repetition_and_expire_without_refresh_from_weaker_peaks():
    history = CandidateHistory(45)
    candidate = {
        "band": "5.8ghz", "viable": True, "frequency_mhz": 5780,
        "antenna_angle_deg": 359, "score": 20,
    }
    history.observe(candidate, 0)
    assert not history.candidates(0)
    history.observe({**candidate, "antenna_angle_deg": 2}, 0.4)
    assert history.candidates(1)["5.8ghz"]["age_seconds"] == pytest.approx(0.6)
    history.observe({**candidate, "score": 10}, 1)
    assert history.candidates(1)["5.8ghz"]["score"] == 20
    assert not history.candidates(45.4)


@pytest.mark.parametrize("change", [
    {"frequency_mhz": 5820}, {"antenna_angle_deg": 90}, {"viable": False},
])
def test_unrelated_or_nonviable_sweep_does_not_confirm_candidate(change):
    history = CandidateHistory()
    candidate = {
        "band": "5.8ghz", "viable": True, "frequency_mhz": 5780,
        "antenna_angle_deg": 0, "score": 20,
    }
    history.observe(candidate, 0)
    history.observe({**candidate, **change}, 0.4)
    assert not history.candidates(1)
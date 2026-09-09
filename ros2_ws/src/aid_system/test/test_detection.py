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
import pytest

from aid_system.rf_source import scan_config


def test_known_bands_select_correct_modules():
    assert scan_config("2.4").expansion
    assert not scan_config("5.8").expansion


def test_custom_frequency_selects_module_and_checks_gap():
    assert scan_config("custom", 2450.0, 20.0).expansion
    assert not scan_config("custom", 5745.0, 20.0).expansion
    with pytest.raises(ValueError):
        scan_config("custom", 4000.0, 20.0)
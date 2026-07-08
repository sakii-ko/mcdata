import pytest

from mcdata.render.pipeline import CaptureSettings


def test_capture_settings_uses_profile_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MCDATA_CAPTURE_SIZE",
        "MCDATA_CAPTURE_FPS",
        "MCDATA_CAPTURE_DESKTOP",
        "MCDATA_HIDE_HUD",
        "MCDATA_VIEW_SETTLE_SEC",
        "MCDATA_CAPTURE_READY_DELAY",
        "DISPLAY",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = CaptureSettings.from_env(
        {"width": 854, "height": 480, "capture_fps": 24, "capture_ready_delay_sec": 15}
    )

    assert settings.width == 854
    assert settings.height == 480
    assert settings.fps == 24
    assert settings.display == ":0"
    assert settings.desktop is False
    assert settings.hide_hud is False
    assert settings.view_settle_sec == 1.0
    assert settings.ready_delay_sec == 15.0


def test_capture_settings_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCDATA_CAPTURE_SIZE", "1920x1080")
    monkeypatch.setenv("MCDATA_CAPTURE_FPS", "30")
    monkeypatch.setenv("MCDATA_CAPTURE_DESKTOP", "1")
    monkeypatch.setenv("MCDATA_HIDE_HUD", "true")
    monkeypatch.setenv("MCDATA_VIEW_SETTLE_SEC", "2.5")
    monkeypatch.setenv("MCDATA_CAPTURE_READY_DELAY", "3")
    monkeypatch.setenv("DISPLAY", ":77")

    settings = CaptureSettings.from_env(
        {"width": 854, "height": 480, "capture_fps": 24, "capture_ready_delay_sec": 15}
    )

    assert settings.width == 1920
    assert settings.height == 1080
    assert settings.fps == 30
    assert settings.display == ":77"
    assert settings.desktop is True
    assert settings.hide_hud is True
    assert settings.view_settle_sec == 2.5
    assert settings.ready_delay_sec == 3.0


def test_capture_settings_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCDATA_CAPTURE_SIZE", "wide")
    with pytest.raises(RuntimeError, match="MCDATA_CAPTURE_SIZE"):
        CaptureSettings.from_env({"width": 854, "height": 480})

    monkeypatch.setenv("MCDATA_CAPTURE_SIZE", "1280x720")
    monkeypatch.setenv("MCDATA_CAPTURE_FPS", "0")
    with pytest.raises(RuntimeError, match="MCDATA_CAPTURE_FPS"):
        CaptureSettings.from_env({"width": 854, "height": 480})

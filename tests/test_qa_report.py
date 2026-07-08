import importlib

from mcdata.qa import report


def test_bilinear_filter_supports_pillow_without_resampling(monkeypatch) -> None:
    monkeypatch.delattr(report.Image, "Resampling", raising=False)

    reloaded = importlib.reload(report)

    assert reloaded._BILINEAR == reloaded.Image.BILINEAR
    importlib.reload(report)

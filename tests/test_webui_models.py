"""Tests for SubmitForm parsing + fetch_kwargs mapping."""
from __future__ import annotations

import pytest

from sharktopus.webui.models import SubmitForm, parse_submit_form


def test_parse_range_mode_happy_path():
    form, errors = parse_submit_form({
        "name": "nice",
        "mode": "range",
        "start": "2024010200", "end": "2024010318", "step": "6",
        "ext": "24", "interval": "3",
        "lat_s": "-28", "lat_n": "-18", "lon_w": "-48", "lon_e": "-36",
        "priority": "aws_crop gcloud",
        "variables": "TMP, UGRD, VGRD",
        "levels": "500 mb, 850 mb",
    })
    assert errors == []
    assert form.name == "nice"
    assert form.mode == "range"
    assert form.priority == ["aws_crop", "gcloud"]
    assert "TMP" in form.variables
    assert "500 mb" in form.levels


def test_parse_list_mode_happy_path():
    form, errors = parse_submit_form({
        "mode": "list",
        "timestamps": "2024010200, 2024010206",
        "lat_s": "-10", "lat_n": "0", "lon_w": "-50", "lon_e": "-40",
    })
    assert errors == []
    assert form.timestamps == ["2024010200", "2024010206"]


def test_parse_rejects_bad_timestamp():
    _form, errors = parse_submit_form({
        "mode": "list",
        "timestamps": "2024-01-02",
        "lat_s": "-10", "lat_n": "0", "lon_w": "-50", "lon_e": "-40",
    })
    assert any("YYYYMMDDHH" in e for e in errors)


def test_parse_rejects_bbox_inversion():
    _form, errors = parse_submit_form({
        "mode": "range",
        "start": "2024010200", "end": "2024010206",
        "lat_s": "0", "lat_n": "-10",  # inverted
        "lon_w": "-50", "lon_e": "-40",
    })
    assert any("lat_s" in e for e in errors)


def test_parse_rejects_lon_equality():
    _form, errors = parse_submit_form({
        "mode": "range",
        "start": "2024010200", "end": "2024010206",
        "lat_s": "-10", "lat_n": "0",
        "lon_w": "-40", "lon_e": "-40",  # equal
    })
    assert any("lon_w" in e or "lon_e" in e for e in errors)


def test_parse_missing_range_args_complains():
    _form, errors = parse_submit_form({
        "mode": "range",
        "lat_s": "-10", "lat_n": "0", "lon_w": "-50", "lon_e": "-40",
    })
    assert any("start" in e or "end" in e for e in errors)


def test_parse_missing_list_complains():
    _form, errors = parse_submit_form({
        "mode": "list",
        "lat_s": "-10", "lat_n": "0", "lon_w": "-50", "lon_e": "-40",
    })
    assert any("at least one timestamp" in e for e in errors)


def test_to_fetch_kwargs_preserves_priority():
    form = SubmitForm(
        mode="list",
        timestamps=["2024010200"],
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        priority=["aws_crop", "nomads"],
    )
    kw = form.to_fetch_kwargs()
    assert kw["priority"] == ("aws_crop", "nomads")
    assert kw["timestamps"] == ["2024010200"]
    assert kw["lat_s"] == -10.0


def test_to_fetch_kwargs_spread_mapping():
    form = SubmitForm(
        mode="list", timestamps=["2024010200"],
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        spread="spread",
    )
    assert form.to_fetch_kwargs()["spread"] is True

    form.spread = "classic"
    assert form.to_fetch_kwargs()["spread"] is False

    form.spread = "auto"
    assert "spread" not in form.to_fetch_kwargs()


def test_parse_rejects_start_not_before_end():
    _form, errors = parse_submit_form({
        "mode": "range",
        "start": "2024010206", "end": "2024010200",  # reversed
        "lat_s": "-10", "lat_n": "0", "lon_w": "-50", "lon_e": "-40",
    })
    assert any("earlier than end" in e for e in errors)


def test_parse_rejects_start_equal_end():
    _form, errors = parse_submit_form({
        "mode": "range",
        "start": "2024010200", "end": "2024010200",  # equal
        "lat_s": "-10", "lat_n": "0", "lon_w": "-50", "lon_e": "-40",
    })
    assert any("earlier than end" in e for e in errors)


def test_parse_rejects_start_before_earliest_gfs():
    _form, errors = parse_submit_form({
        "mode": "range",
        "start": "2010010100", "end": "2024010200",  # pre-2015 floor
        "lat_s": "-10", "lat_n": "0", "lon_w": "-50", "lon_e": "-40",
    })
    assert any("earliest" in e.lower() or "RDA" in e for e in errors)


def test_parse_rejects_list_timestamp_before_earliest_gfs():
    _form, errors = parse_submit_form({
        "mode": "list",
        "timestamps": "2010010100, 2024010200",
        "lat_s": "-10", "lat_n": "0", "lon_w": "-50", "lon_e": "-40",
    })
    assert any("earliest" in e.lower() or "RDA" in e for e in errors)


def test_submit_form_round_trips_json():
    form = SubmitForm(
        name="round",
        mode="list",
        timestamps=["2024010200"],
        lat_s=-10, lat_n=0, lon_w=-50, lon_e=-40,
        priority=["aws_crop"],
    )
    back = SubmitForm.from_json(form.to_json())
    assert back.name == "round"
    assert back.priority == ["aws_crop"]
    assert back.timestamps == ["2024010200"]


def test_parse_rejects_lat_out_of_range():
    _form, errors = parse_submit_form({
        "mode": "list", "timestamps": "2024010200",
        "lat_s": "-95", "lat_n": "10", "lon_w": "-50", "lon_e": "-40",
    })
    assert any("lat_s" in e and "[-90, 90]" in e for e in errors)


def test_parse_rejects_lat_n_out_of_range():
    _form, errors = parse_submit_form({
        "mode": "list", "timestamps": "2024010200",
        "lat_s": "-10", "lat_n": "91", "lon_w": "-50", "lon_e": "-40",
    })
    assert any("lat_n" in e and "[-90, 90]" in e for e in errors)


def test_parse_rejects_lon_inversion():
    _form, errors = parse_submit_form({
        "mode": "list", "timestamps": "2024010200",
        "lat_s": "-10", "lat_n": "0",
        "lon_w": "-40", "lon_e": "-50",  # inverted
    })
    assert any("lon_w" in e and "west" in e for e in errors)


def test_parse_rejects_lon_out_of_range_180():
    _form, errors = parse_submit_form({
        "mode": "list", "timestamps": "2024010200",
        "lat_s": "-10", "lat_n": "0",
        "lon_w": "-200", "lon_e": "-40",
        "lon_convention": "-180..180",
    })
    assert any("lon_w" in e and "-180" in e for e in errors)


def test_parse_accepts_lon_360_convention():
    form, errors = parse_submit_form({
        "mode": "list", "timestamps": "2024010200",
        "lat_s": "-10", "lat_n": "0",
        "lon_w": "300", "lon_e": "320",
        "lon_convention": "0..360",
    })
    assert errors == []
    assert form.lon_convention == "0..360"
    assert form.lon_w == 300.0
    assert form.lon_e == 320.0


def test_parse_rejects_lon_360_out_of_range():
    _form, errors = parse_submit_form({
        "mode": "list", "timestamps": "2024010200",
        "lat_s": "-10", "lat_n": "0",
        "lon_w": "-10", "lon_e": "320",  # negative invalid in 0..360
        "lon_convention": "0..360",
    })
    assert any("lon_w" in e and "0" in e and "360" in e for e in errors)


def test_parse_rejects_unknown_lon_convention():
    _form, errors = parse_submit_form({
        "mode": "list", "timestamps": "2024010200",
        "lat_s": "-10", "lat_n": "0", "lon_w": "-50", "lon_e": "-40",
        "lon_convention": "-360..360",
    })
    assert any("lon_convention" in e for e in errors)

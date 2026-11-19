from typing import Any

import pytest

from backend.app.api.requirements import _public_requirement, _validate


@pytest.mark.parametrize("weight", [0, 100, 12.5, "20"])
def test_requirement_points_accept_values_between_zero_and_one_hundred(
    weight: Any,
) -> None:
    assert _validate({"weight": weight}) is None


@pytest.mark.parametrize(
    "weight",
    [-0.1, 100.1, float("nan"), float("inf"), "invalid", True, None],
)
def test_requirement_points_reject_values_outside_zero_to_one_hundred(
    weight: Any,
) -> None:
    assert _validate({"weight": weight}) == "Points must be between 0 and 100."


def test_photo_rules_accept_visa_specific_thresholds_and_manual_review():
    assert _validate(
        {
            "code": "applicant_photo",
            "acceptedFormats": ["jpg", "jpeg", "png"],
            "photoRules": {
                "minWidth": 800,
                "minHeight": 1000,
                "minSharpness": 60,
                "minBrightness": 30,
                "maxBrightness": 230,
                "faceCenterTolerance": 0.2,
                "minFaceHeightRatio": 0.25,
                "manualReviewRequired": True,
            },
        }
    ) is None


def test_legacy_face_count_rule_is_not_configurable_or_returned():
    requirement = {
        "photoRules": {
            "minWidth": 600,
            "exactlyOneFace": True,
            "manualReviewRequired": False,
        }
    }

    assert _validate({"photoRules": requirement["photoRules"]}) is not None
    assert "exactlyOneFace" not in _public_requirement(requirement)["photoRules"]
    assert requirement["photoRules"]["exactlyOneFace"] is True


@pytest.mark.parametrize("max_file_size", [float("nan"), float("inf"), 1.5])
def test_requirement_file_size_must_be_a_finite_integer(max_file_size):
    assert _validate({"maxFileSize": max_file_size}) is not None


@pytest.mark.parametrize(
    "payload",
    [
        {"photoRules": {"minWidth": 0}},
        {"photoRules": {"faceCenterTolerance": 0.6}},
        {"photoRules": {"manualReviewRequired": "yes"}},
        {"photoRules": {"exactlyOneFace": True}},
        {"photoRules": {"minBrightness": 240, "maxBrightness": 220}},
        {"code": "photo", "acceptedFormats": ["webp"]},
    ],
)
def test_invalid_photo_rules_and_formats_are_rejected(payload):
    assert _validate(payload) is not None

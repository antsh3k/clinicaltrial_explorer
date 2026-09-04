"""Ingest unit tests on hand-built records (no zip, no network)."""

from datetime import date

import pytest
from pydantic import ValidationError

from ct_landscape.ingest import Batch, parse_partial_date, parse_study


def _study(**over):
    base = {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT00000001",
                "briefTitle": "T",
                "organization": {"fullName": "Acme Pharma Inc.", "class": "INDUSTRY"},
            },
            "statusModule": {
                "overallStatus": "COMPLETED",
                "startDateStruct": {"date": "2014-08-25", "type": "ACTUAL"},
                "completionDateStruct": {"date": "2021-05", "type": "ACTUAL"},
                "lastUpdatePostDateStruct": {"date": "2022-06-13", "type": "ACTUAL"},
            },
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": "Acme Pharma Inc.", "class": "INDUSTRY"},
                "collaborators": [{"name": "Some University", "class": "OTHER"}],
            },
            "conditionsModule": {"conditions": ["Non-Small Cell Lung Carcinoma"], "keywords": ["PD-L1"]},
            "designModule": {
                "studyType": "INTERVENTIONAL",
                "phases": ["PHASE2", "PHASE3"],
                "enrollmentInfo": {"count": 305, "type": "ACTUAL"},
            },
            "armsInterventionsModule": {
                "armGroups": [
                    {"label": "Pembro", "type": "EXPERIMENTAL", "interventionNames": ["Drug: Pembrolizumab"]},
                    {
                        "label": "Chemo",
                        "type": "ACTIVE_COMPARATOR",
                        "interventionNames": ["Drug: Carboplatin"],
                    },
                ],
                "interventions": [
                    {
                        "type": "DRUG",
                        "name": "Pembrolizumab",
                        "armGroupLabels": ["Pembro"],
                        "otherNames": ["MK-3475", "KEYTRUDA®"],
                    },
                    {"type": "DRUG", "name": "Carboplatin", "armGroupLabels": ["Chemo"]},
                ],
            },
            "eligibilityModule": {
                "sex": "ALL",
                "minimumAge": "18 Years",
                "stdAges": ["ADULT", "OLDER_ADULT"],
            },
        },
        "derivedSection": {
            "conditionBrowseModule": {
                "meshes": [{"id": "D002289", "term": "Carcinoma, Non-Small-Cell Lung"}],
                "ancestors": [{"id": "D009369", "term": "Neoplasms"}],
            }
        },
        "hasResults": True,
        # this junk must be ignored, never loaded
        "resultsSection": {"huge": "blob"},
        "documentSection": {"x": 1},
    }
    base.update(over)
    return base


@pytest.mark.parametrize(
    "s, expected",
    [
        ("2014-08-25", (date(2014, 8, 25), "day")),
        ("2021-05", (date(2021, 5, 1), "month")),
        ("1999", (date(1999, 1, 1), "year")),
        ("", (None, None)),
        (None, (None, None)),
        ("2021-13", (None, None)),
        ("May 2021", (None, None)),
    ],
)
def test_partial_dates_pad_to_period_start(s, expected):
    assert parse_partial_date(s) == expected


def test_parse_study_loads_every_raw_table():
    out = Batch()
    parse_study(_study(), out)
    row = out.rows["studies"][0]
    assert row[0] == "NCT00000001"
    assert row[7] == "PHASE3"  # combined phase rounds up
    assert row[12] == "2021-05" and row[16] == date(2021, 5, 1)  # raw kept, parsed padded
    assert row[18] == "month"  # coarsest precision among present dates
    assert row[26] == ["ADULT", "OLDER_ADULT"]
    assert [r[1:] for r in out.rows["sponsors"]] == [
        ("lead", "Acme Pharma Inc.", "INDUSTRY"),
        ("collaborator", "Some University", "OTHER"),
    ]
    assert [r[2] for r in out.rows["intervention_other_names"]] == ["MK-3475", "KEYTRUDA®"]
    assert sorted(out.rows["arm_interventions"]) == [
        ("NCT00000001", 0, 0, "label"),
        ("NCT00000001", 1, 1, "label"),
    ]
    assert {(r[1], r[2]) for r in out.rows["mesh_terms"]} == {
        ("condition", "mesh"),
        ("condition", "ancestor"),
    }
    assert out.census["n_loaded"] == 1
    assert out.census["n_arm_links_via_label"] == 2
    assert out.census["n_no_derived_mesh_intervention"] == 1


def test_arm_join_falls_back_to_intervention_names_when_labels_absent():
    s = _study()
    for iv in s["protocolSection"]["armsInterventionsModule"]["interventions"]:
        iv.pop("armGroupLabels")
    out = Batch()
    parse_study(s, out)
    assert sorted(out.rows["arm_interventions"]) == [
        ("NCT00000001", 0, 0, "name"),
        ("NCT00000001", 1, 1, "name"),
    ]
    assert out.census["n_arm_links_via_name"] == 2
    assert out.census["n_arm_links_via_label"] == 0


def test_missing_modules_are_counted_not_errors():
    s = _study()
    ps = s["protocolSection"]
    for m in ("armsInterventionsModule", "conditionsModule", "eligibilityModule", "designModule"):
        ps.pop(m)
    ps["statusModule"].pop("lastUpdatePostDateStruct")
    s.pop("derivedSection")
    out = Batch()
    parse_study(s, out)
    c = out.census
    assert c["n_loaded"] == 1
    for k in ("n_no_arms", "n_no_interventions", "n_no_conditions", "n_no_phases", "n_no_enrollment"):
        assert c[k] == 1, k
    assert c["n_no_last_update_date"] == 1
    assert out.rows["studies"][0][7] is None  # no phases → NULL, never a default


def test_missing_nct_id_is_a_parse_failure():
    s = _study()
    s["protocolSection"]["identificationModule"].pop("nctId")
    with pytest.raises(ValidationError):
        parse_study(s, Batch())

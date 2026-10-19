"""Source-serving arithmetic and per100g policy references have separate roles."""
import copy
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from audit_engine.label_check_v2.engine import run


def yellow():
    return json.loads((Path(__file__).parent / "examples/yellow_72g_transcription.json").read_text(encoding="utf-8"))


def rule(data, identifier):
    return next(item for item in run(data)["findings"] if item["rule_id"] == identifier)


def test_72g_energy_uses_source_amounts_without_changing_rounding_decision():
    data = yellow()
    before = copy.deepcopy(data)
    item = rule(data, "ENERGY-ARITHMETIC")
    assert item["status"] == "REVIEW"
    details = item["details"]
    for key, value in {"computed_kj": "1418", "shown_kj": "1420", "difference_kj": "2",
                       "source_rounding_interval_kj": "4.45", "source_pass_tolerance_kj": "0.36"}.items():
        assert Decimal(details[key]) == Decimal(value)
    assert details["basis"] == "per_serving" and details["serving_g"] == "72"
    assert details["formula"] == "4.1×17 + 13.5×37 + 48×17 + 4.1×8"
    assert "每份72g" in item["message"]
    assert "1418 kJ" in item["message"] and "1420 kJ" in item["message"] and "相差2 kJ" in item["message"]
    assert not re.search(r"\d+\.\d{3,}", item["message"])
    assert data == before
    report = run(data)
    assert report["output_header"] == data["table_header"]
    assert report["calculation_bases"] == {"energy": "per_serving", "nrv_reference": "per_100g", "claims": "rule_specific"}
    assert not any(f["rule_id"] == "COURSE-100G-001" for f in report["findings"])
    # Exact normalized values remain available for independent rules and audit.
    assert Decimal(details["shown_kj_per_100g"]) == Decimal("1420") * 100 / 72


def test_nrv_reference_is_not_compared_to_the_printed_serving_percentage():
    item = rule(yellow(), "NRV-100G-ENERGY")
    assert item["status"] == "REVIEW"
    assert item["details"]["expected_percent"] == "23"
    assert item["details"]["source_nrv_percent"] == "17"
    assert item["details"]["directly_comparable"] is False
    assert "每100g项目预检参考值为23%" in item["message"]
    assert "每份72g标示17%" in item["message"]
    assert "须整表改写" not in item["message"]


@pytest.mark.parametrize("serving", ["10", "72", "200"])
@pytest.mark.parametrize("delta100,status", [("0.5", "PASS"), ("0.501", "REVIEW"), ("100", "FAIL")])
def test_pass_threshold_is_scaled_and_display_rounding_cannot_change_the_verdict(serving, delta100, status):
    data = yellow()
    data["serving_g"] = serving
    data["table_header"] = "每份" + serving + "g"
    computed = Decimal("1418")
    data["nutrients"]["energy"]["value"] = str(computed + Decimal(delta100) * Decimal(serving) / 100)
    item = rule(data, "ENERGY-ARITHMETIC")
    assert item["status"] == status
    assert Decimal(item["details"]["source_pass_tolerance_kj"]) == Decimal("0.5") * Decimal(serving) / 100


def test_per100g_arithmetic_and_nrv_still_use_per100g():
    data = yellow()
    data.update(basis="per_100g", table_header="每100克")
    data["nutrients"]["energy"]["value"] = "1418"
    data["nutrients"]["protein"]["nrv_percent"] = "7"
    energy = rule(data, "ENERGY-ARITHMETIC")
    assert energy["status"] == "PASS" and energy["details"]["basis"] == "per_100g"
    assert "每100g" in energy["message"]
    nrv = rule(data, "NRV-100G-PROTEIN")
    assert nrv["status"] == "PASS" and nrv["details"]["directly_comparable"] is True
    data["nutrients"]["protein"]["nrv_percent"] = "9"
    assert rule(data, "NRV-100G-PROTEIN")["status"] == "FAIL"


def test_claim_thresholds_keep_their_own_basis_for_serving_tables():
    data = yellow()
    data["claims"] = ["low_sugar", "high_protein"]
    data["nutrients"]["sugar"]["value"] = "4.8"  # 6.67g/100g, above the 5g/100g threshold
    sugar = rule(data, "CLAIM-LOW_SUGAR")
    assert sugar["status"] == "FAIL"
    assert sugar["details"]["basis"] == "per_100g" and "每100g" in sugar["message"]
    data["serving_g"] = "10"
    data["nutrients"]["protein"]["value"] = "5"  # 50g/100g, above 12g/100g
    protein = rule(data, "CLAIM-HIGH_PROTEIN")
    assert protein["status"] == "PASS"
    assert protein["details"]["threshold_per_100g"] == "12"
    assert protein["details"]["threshold_per_420kj"] == "6"
    assert "每100g或每420kJ" in protein["message"]


def test_missing_energy_message_keeps_source_basis_and_unknown_serving_is_blocked():
    data = yellow()
    del data["nutrients"]["energy"]
    assert "每份72g可计算值为1418 kJ" in rule(data, "ENERGY-MISSING")["message"]
    del data["serving_g"]
    report = run(data)
    assert any(f["rule_id"] == "INPUT-BASIS" for f in report["findings"])
    assert not any(f["rule_id"] == "ENERGY-ARITHMETIC" for f in report["findings"])

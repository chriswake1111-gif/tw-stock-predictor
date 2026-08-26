from __future__ import annotations

import pytest

from src.collectors.universe_collectors import (
    UniverseCollector,
    UniverseSourceRejected,
    parse_universe_payload,
)


TWSE_NEWLISTING = {
    "Code": "7855",
    "Company": "和運租車",
    "ApplicationDate": "1150414",
    "Chairman": "劉源森",
    "AmountofCapital ": "1925279",
    "CommitteeDate": "1150522",
    "ApprovedDate": "1150616",
    "AgreementDate": "1150624",
    "ListingDate": "",
    "ApprovedListingDate": "1150811",
    "Underwriter": "台新",
    "UnderwritingPrice": "42.00",
    "Note": "",
}

TWSE_TERMINATION = {
    "DelistingDate": "115/06/23",
    "Company": "森崴能源",
    "Code": "6806",
}


def _response(payload):
    class Response:
        status_code = 200

        def json(self):
            return payload

    return Response()


def test_twse_newlisting_official_array_and_exact_fields_are_fail_closed():
    accepted = UniverseCollector(lambda *_args, **_kwargs: _response([TWSE_NEWLISTING])).fetch_official(
        "twse.company.newlisting", url="https://openapi.twse.com.tw/v1/opendata/company/newlisting"
    )
    assert accepted.status == "accepted"
    assert accepted.rows[0]["official_code"] == "7855"
    assert accepted.query_dimensions["source_envelope"] == "array"

    for changed in (
        {**TWSE_NEWLISTING, "Code": None},
        {key: value for key, value in TWSE_NEWLISTING.items() if key != "ApprovedDate"},
        {**TWSE_NEWLISTING, "ApprovedDateRenamed": TWSE_NEWLISTING["ApprovedDate"]},
    ):
        result = UniverseCollector(lambda *_args, payload=[changed], **_kwargs: _response(payload)).fetch_official(
            "twse.company.newlisting", url="https://openapi.twse.com.tw/v1/opendata/company/newlisting"
        )
        assert result.status == "schema_changed"

    with pytest.raises(UniverseSourceRejected, match="source_schema_changed"):
        parse_universe_payload("twse.company.newlisting", {"data": [TWSE_NEWLISTING]})


def test_twse_termination_official_array_has_no_invented_reason_field():
    accepted = parse_universe_payload("twse.company.suspendListingCsvAndHtml", [TWSE_TERMINATION])
    assert accepted[0]["official_code"] == "6806"
    assert "reason" not in accepted[0]

    for changed in (
        {key: value for key, value in TWSE_TERMINATION.items() if key != "DelistingDate"},
        {key: value for key, value in TWSE_TERMINATION.items() if key != "Code"},
        {**TWSE_TERMINATION, "reason": "not an official field"},
    ):
        with pytest.raises(UniverseSourceRejected, match="source_schema_changed"):
            parse_universe_payload("twse.company.suspendListingCsvAndHtml", [changed])

    with pytest.raises(UniverseSourceRejected, match="source_schema_changed"):
        parse_universe_payload("twse.company.suspendListingCsvAndHtml", {"data": [TWSE_TERMINATION]})


def test_tpex_operational_contracts_use_source_fields_not_generic_state():
    history = [{
        "Date": "1150821", "Serial": "1", "SecuritiesCompanyCode": "1788", "CompanyName": "杏昌",
        "DateOfSuspendedTrading": "1150618", "TimeOfSuspendedTrading": "080000",
        "DateOfResumedTrading": "", "TimeOfResumedTrading": "",
    }]
    today = [{
        "Date": "20260821", "SecuritiesCompanyCode": "1788", "CompanyName": "杏昌",
        "DateOfSuspendedTrading": "20260821", "TimeOfSuspendedTrading": "080000",
        "DateOfResumedTrading": "", "TimeOfResumedTrading": "",
    }]
    cmode = [{
        "Date": "1150821", "SecuritiesCompanyCode": "2067", "CompanyName": "嘉鋼",
        "AlteredTrading": "Ｙ", "PeriodicTrading": "", "ManagedStock": "",
        "MatchingFrequency": "", "SuspensionOfTrading": "", " FinancialAnnouncements": "Ｙ",
    }]
    assert parse_universe_payload("tpex.spendi.history", history)
    assert parse_universe_payload("tpex.spendi.today", today)
    parsed_cmode = parse_universe_payload("tpex.cmode", cmode)
    assert parsed_cmode[0]["AlteredTrading"] == "Ｙ"
    assert "listing_status" not in parsed_cmode[0]
    assert "status" not in parsed_cmode[0]

    with pytest.raises(UniverseSourceRejected, match="source_schema_changed"):
        parse_universe_payload("tpex.spendi.history", [{"Date": "1150821", "SecuritiesCompanyCode": "1788", "status": "normal"}])
    with pytest.raises(UniverseSourceRejected, match="source_schema_changed"):
        parse_universe_payload("tpex.cmode", [{"Date": "1150821", "SecuritiesCompanyCode": "2067", "CompanyName": "嘉鋼", "status": "normal"}])


def test_schema_fingerprint_is_observed_evidence_but_expected_contract_is_the_gate():
    valid_payload = [{"出表日期": "1150820", "公司代號": "2330", "公司名稱": "台積電"}]
    valid = UniverseCollector(lambda *_args, **_kwargs: _response(valid_payload)).fetch_official(
        "twse.t187ap03_L", url="https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
    )
    assert valid.status == "accepted"
    assert valid.schema_fingerprint
    assert valid.query_dimensions["expected_schema_fingerprint"]

    drift_payload = [{**valid_payload[0], "未核准新欄位": "drift"}]
    drift = UniverseCollector(lambda *_args, **_kwargs: _response(drift_payload)).fetch_official(
        "twse.t187ap03_L",
        url="https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
        schema_fingerprint=valid.schema_fingerprint,
    )
    assert drift.status == "schema_changed"
    assert drift.reason == "source_schema_changed"

    renamed = UniverseCollector(lambda *_args, **_kwargs: _response([{"出表日期": "1150820", "公司代號": "2330", "公司名稱改名": "台積電"}])).fetch_official(
        "twse.t187ap03_L", url="https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
    )
    assert renamed.status == "schema_changed"

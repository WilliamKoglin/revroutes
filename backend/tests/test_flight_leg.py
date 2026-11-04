import json
from backend.app.services.flight_leg import parse_flights

def test_parses_nonstop_flights():
    data = json.loads(FIXTURE.read_text())
    flights = parse_flights(data)
    assert len(flights) >= 1
    f = flights[0]
    assert f["carrier"] == "AA"
    assert f["origin"] == "IAH"
    assert f["dest"] == "DFW"
    assert f["stops"] == 0


def test_model_features_shape():
    data = json.loads(FIXTURE.read_text())
    f = parse_flights(data)[0]
    mf = f["model_features"]
    assert set(mf) == {"Reporting_Airline", "Origin", "Dest",
                       "Month", "DayOfWeek", "DayofMonth", "DepHour"}
    assert mf["Month"] == 12
    assert mf["DayofMonth"] == 15
    assert 0 <= mf["DepHour"] <= 23
from operation_lens_v2.ingestion.normaliser import normalise


def test_person_normalisation() -> None:
    assert normalise("dc webb, marcus", "PERSON") == "Marcus Webb"


def test_vehicle_normalisation() -> None:
    assert normalise("rx71kld", "VEHICLE") == "RX71 KLD"

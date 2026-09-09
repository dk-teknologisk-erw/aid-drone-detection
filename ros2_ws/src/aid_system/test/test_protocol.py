from aid_system.protocol import decode_status, encode_command


def test_mcu_protocol_round_trip_shapes():
    command = encode_command("speed", speed_dps=30.0)
    assert command == b'{"cmd":"speed","speed_dps":30.0}\n'
    status = decode_status(b'{"type":"status","angle_deg":12.5}')
    assert status["angle_deg"] == 12.5
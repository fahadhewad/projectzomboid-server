import socket
import struct
import threading

import pytest

from pzops import rcon


def test_packet_round_trip():
    encoded = rcon.encode_packet(7, rcon.SERVERDATA_EXECCOMMAND, 'servermsg "hi"')
    size = struct.unpack("<i", encoded[:4])[0]

    assert size == len(encoded) - 4
    assert rcon.decode_packet(encoded[4:]) == (7, rcon.SERVERDATA_EXECCOMMAND, 'servermsg "hi"')


def test_encoding_is_utf8_safe():
    """Player names and mod titles are not ASCII."""
    encoded = rcon.encode_packet(1, 0, "kicked Bex—done")
    assert rcon.decode_packet(encoded[4:])[2] == "kicked Bex—done"


class FakeRconServer:
    """A socket server speaking just enough Source RCON to drive the client."""

    def __init__(self, password="secret"):
        self.password = password
        self.received = []
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _read_packet(self, conn):
        header = conn.recv(4)
        if not header:
            return None
        size = struct.unpack("<i", header)[0]
        return rcon.decode_packet(conn.recv(size))

    def _serve(self):
        conn, _ = self._sock.accept()
        with conn:
            while True:
                packet = self._read_packet(conn)
                if packet is None:
                    return
                packet_id, packet_type, body = packet
                if packet_type == rcon.SERVERDATA_AUTH:
                    # The protocol signals a bad password by replying with id -1.
                    reply_id = packet_id if body == self.password else -1
                    conn.sendall(rcon.encode_packet(reply_id, rcon.SERVERDATA_AUTH_RESPONSE, ""))
                else:
                    self.received.append(body)
                    conn.sendall(
                        rcon.encode_packet(packet_id, rcon.SERVERDATA_RESPONSE_VALUE, f"ok: {body}")
                    )

    def close(self):
        self._sock.close()


@pytest.fixture
def server():
    fake = FakeRconServer()
    yield fake
    fake.close()


def test_command_round_trip(server):
    with rcon.RconClient("127.0.0.1", server.port, "secret", timeout=5) as client:
        assert client.command("players") == "ok: players"
    assert server.received == ["players"]


def test_wrong_password_raises_auth_error(server):
    with pytest.raises(rcon.RconAuthError):
        rcon.RconClient("127.0.0.1", server.port, "wrong", timeout=5).connect()


def test_try_command_swallows_a_refused_connection():
    """An announcement must never take down the backup daemon."""
    assert rcon.try_command("127.0.0.1", 1, "pw", "servermsg hi", timeout=0.5) is None


def test_try_command_without_a_password_is_a_no_op():
    assert rcon.try_command("127.0.0.1", 27015, "", "servermsg hi") is None


def test_try_command_succeeds_against_a_live_server(server):
    result = rcon.try_command("127.0.0.1", server.port, "secret", "servermsg hi")
    assert result == "ok: servermsg hi"

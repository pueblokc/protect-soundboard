import pytest

import protect_engine
from protect_engine import ProtectEngine

CFG = {"devices": {"horn_id": "H1", "horn_mac": "AA", "siren_id": "S1"}, "max_volume": 60}


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, script):
        self.script = list(script)  # list of Resp for .request calls
        self.headers, self.verify, self.requests, self.logins = {}, True, [], 0

    def post(self, url, **kw):  # login
        self.logins += 1
        return Resp(200, headers={"X-CSRF-Token": f"csrf{self.logins}"})

    def request(self, method, url, **kw):
        self.requests.append((method, url, kw.get("json"), dict(self.headers)))
        return self.script.pop(0)


def mk(script):
    e = ProtectEngine("nvr.example", "u", "p", CFG)
    e.s = FakeSession(script)
    return e


def test_login_sets_csrf():
    e = mk([])
    assert e.login() and e.s.headers["X-CSRF-Token"] == "csrf1"


def test_rotated_csrf_token_is_used_next_request():
    e = mk([Resp(200, headers={"X-Updated-Csrf-Token": "rotated"}), Resp(200)])
    e.set_volume("horn", 10)
    e.set_volume("horn", 20)
    assert e.s.requests[1][3]["X-CSRF-Token"] == "rotated"


def test_401_relogs_in_once_and_retries():
    e = mk([Resp(401), Resp(200)])
    ok, v = e.set_volume("siren", 30)
    assert ok and v == 30 and e.s.logins == 2 and len(e.s.requests) == 2


def test_volume_clamped_to_cap_unless_allow_loud():
    e = mk([Resp(200), Resp(200), Resp(200)])
    assert e.set_volume("horn", 95)[1] == 60
    assert e.set_volume("horn", 95, allow_loud=True)[1] == 95
    assert e.set_volume("horn", -5)[1] == 0
    assert [r[2] for r in e.s.requests] == [{"volume": 60}, {"volume": 95}, {"volume": 0}]


def test_volume_target_validated():
    e = mk([])
    with pytest.raises(ValueError):
        e.set_volume("everything", 10)


def test_siren_duration_clamped_and_ms():
    e = mk([Resp(200), Resp(200)])
    assert e.activate_siren(99999)["duration"] == protect_engine.MAX_SIREN_SECONDS
    e.activate_siren(-3)
    durs = [r[2]["sirenStatus"]["duration"] for r in e.s.requests]
    assert durs == [protect_engine.MAX_SIREN_SECONDS * 1000, 1000]


def test_siren_with_volume_sets_volume_first():
    e = mk([Resp(200), Resp(200)])
    e.activate_siren(10, volume=100)
    assert e.s.requests[0][1].endswith("/sirens/S1") and e.s.requests[0][2] == {"volume": 60}
    assert e.s.requests[1][2] == {"sirenStatus": {"isActive": True, "duration": 10000}}


def test_stop_siren():
    e = mk([Resp(200)])
    assert e.stop_siren()["ok"]
    assert e.s.requests[0][2] == {"sirenStatus": {"isActive": False}}


def test_status_tolerates_missing_and_null_fields():
    boot = {"speakers": [{"name": "nameless-no-id"}, {"id": "H1", "name": "Horn", "volume": 40, "state": "CONNECTED"}],
            "sirens": [{"id": "S1", "name": "Siren", "volume": 30, "state": "DISCONNECTED", "sirenStatus": None}]}
    st = mk([Resp(200, boot)]).get_status()
    assert st["horn"] == {"name": "Horn", "volume": 40, "connected": True}
    assert st["siren"]["active"] is False and st["siren"]["connected"] is False


def test_status_devices_absent():
    st = mk([Resp(200, {"speakers": None})]).get_status()
    assert st["horn"]["connected"] is False and st["siren"]["active"] is False


def test_status_http_error_raises_clear_error():
    with pytest.raises(RuntimeError, match="HTTP 503"):
        mk([Resp(503, None)]).get_status()


def test_speak_unifi_payload():
    e = mk([Resp(200)])
    assert e.speak_unifi("hello")["ok"]
    body = e.s.requests[0][2]
    act = body["actions"][0]
    assert act["type"] == "PLAY_TEXT_ON_SPEAKER" and act["metadata"]["text"] == "hello"
    assert act["metadata"]["sources"] == [{"type": "include", "device": "AA"}]

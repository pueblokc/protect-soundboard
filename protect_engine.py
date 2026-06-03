"""Protect control engine (Protect 7.1.60): siren, volume, status, + free UniFi TTS.

Audio playback (arbitrary files + premium TTS) is handled by talkback.py.
This module covers the direct private-API controls and the free built-in TTS
fallback (POST /automations/run = the UI "Test Alarm" dry-run).
"""
import uuid
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class ProtectEngine:
    def __init__(self, host, username, password, cfg):
        self.host = host
        self.api = f"https://{host}/proxy/protect/api"
        self.base = f"https://{host}"
        self.username = username
        self.password = password
        self.cfg = cfg
        self.horn_id = cfg["devices"]["horn_id"]
        self.horn_mac = cfg["devices"]["horn_mac"]
        self.siren_id = cfg["devices"]["siren_id"]
        self.max_volume = int(cfg.get("max_volume", 60))
        self.s = requests.Session()
        self.s.verify = False
        self.csrf = None
        self._auth = False

    def login(self):
        r = self.s.post(f"{self.base}/api/auth/login",
                        json={"username": self.username, "password": self.password}, timeout=15)
        if r.status_code == 200:
            self.csrf = (r.headers.get("X-Updated-Csrf-Token")
                         or r.headers.get("X-CSRF-Token") or r.headers.get("x-csrf-token"))
            if self.csrf:
                self.s.headers["X-CSRF-Token"] = self.csrf
            self._auth = True
            return True
        self._auth = False
        return False

    def _ensure(self):
        if not self._auth and not self.login():
            raise RuntimeError("Protect auth failed")

    def _req(self, method, url, **kw):
        self._ensure()
        kw.setdefault("timeout", 20)
        r = self.s.request(method, url, **kw)
        if r.status_code == 401:
            self._auth = False
            self._ensure()
            r = self.s.request(method, url, **kw)
        return r

    def get_status(self):
        b = self._req("GET", f"{self.api}/bootstrap").json()
        horn = next((x for x in b.get("speakers", []) if x["id"] == self.horn_id), None)
        siren = next((x for x in b.get("sirens", []) if x["id"] == self.siren_id), None)
        return {
            "horn": {"name": horn and horn.get("name"), "volume": horn and horn.get("volume"),
                     "connected": bool(horn) and horn.get("state") == "CONNECTED"},
            "siren": {"name": siren and siren.get("name"), "volume": siren and siren.get("volume"),
                      "active": bool(siren) and siren.get("sirenStatus", {}).get("isActive"),
                      "connected": bool(siren) and siren.get("state") == "CONNECTED"},
            "max_volume": self.max_volume,
        }

    def _clamp(self, vol, allow_loud=False):
        vol = max(0, min(100, int(vol)))
        return vol if allow_loud else min(vol, self.max_volume)

    def set_volume(self, target, volume, allow_loud=False):
        v = self._clamp(volume, allow_loud)
        ep = f"{self.api}/speakers/{self.horn_id}" if target == "horn" else f"{self.api}/sirens/{self.siren_id}"
        r = self._req("PATCH", ep, json={"volume": v})
        return r.status_code == 200, v

    def speak_unifi(self, text):
        """Free built-in TTS via the Test-Alarm dry-run (no save, plays live)."""
        body = {
            "name": "_soundboard", "enable": True, "sources": [],
            "conditions": [{"condition": {"type": "is", "source": "webhook", "value": str(uuid.uuid4())}}],
            "historyConditions": [], "schedules": [],
            "actions": [{"type": "PLAY_TEXT_ON_SPEAKER", "order": -1, "metadata": {
                "text": text, "tone": "welcome", "type": "custom",
                "sources": [{"type": "include", "device": self.horn_mac}]}}],
            "cooldown": {"enable": False, "timeout": 0},
        }
        r = self._req("POST", f"{self.api}/automations/run", json=body, timeout=25)
        return {"ok": r.status_code == 200, "status": r.status_code}

    def activate_siren(self, duration=10, volume=None, allow_loud=False):
        if volume is not None:
            self.set_volume("siren", volume, allow_loud)
        r = self._req("PATCH", f"{self.api}/sirens/{self.siren_id}",
                      json={"sirenStatus": {"isActive": True, "duration": int(duration) * 1000}})
        return {"ok": r.status_code == 200, "status": r.status_code}

    def stop_siren(self):
        r = self._req("PATCH", f"{self.api}/sirens/{self.siren_id}",
                      json={"sirenStatus": {"isActive": False}})
        return {"ok": r.status_code == 200, "status": r.status_code}

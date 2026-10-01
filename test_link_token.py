"""טוקן חדש לכל קישור: הישן בטל, ומכשיר מאושר שנכנס איתו מוסר מהמאושרים."""
import os
import tempfile
from pathlib import Path

import app

LAN = {"REMOTE_ADDR": "192.168.1.20"}


def _setup(tmp: Path):
    app.TOKENS_FILE = tmp / "link_tokens.json"
    app.DEVICES_FILE = tmp / "devices.json"
    app._devices = None
    app._failures.clear()
    app.TOKEN = ""
    app.REMOTE, app.PUBLIC = True, False
    app.LINK_BASE = "http://192.168.1.10:5000/"
    os.environ.pop("APP_TOKEN", None)
    app.load_env = lambda: None          # לא לגעת ב-.env האמיתי
    return app.app.test_client()


def _tok(link: str) -> str:
    return link.split("?t=")[1]


def _blank(r) -> bool:
    return r.get_data(as_text=True) == "<!doctype html><title></title>"


def test_unapproved_device_gets_blank_page(c):
    app.fresh_link()
    r = c.get("/", environ_base=LAN)
    assert r.status_code == 401 and _blank(r)
    r = c.get("/?t=nope", environ_base=LAN)
    assert r.status_code == 401 and _blank(r)


def test_each_link_is_new_and_old_one_stops_working(c):
    first = _tok(app.fresh_link())
    second = _tok(app.fresh_link())
    assert first != second and app.TOKEN == second
    r = c.get(f"/?t={first}", environ_base=LAN)
    assert r.status_code == 401 and _blank(r)
    assert not app.devices()
    r = c.get(f"/?t={second}", environ_base=LAN)
    assert r.status_code == 302 and len(app.devices()) == 1


def test_approved_device_with_old_link_is_removed_until_new_link(c):
    first = _tok(app.fresh_link())
    assert c.get(f"/?t={first}", environ_base=LAN).status_code == 302
    assert len(app.devices()) == 1
    assert c.get("/api/devices", environ_base=LAN).status_code == 200   # מאושר, בלי טוקן
    second = _tok(app.fresh_link())
    r = c.get(f"/?t={first}", environ_base=LAN)                          # קישור ישן
    assert r.status_code == 401 and _blank(r)
    assert not app.devices()
    assert c.get("/api/devices", environ_base=LAN).status_code == 401     # כבר לא מאושר
    assert c.get(f"/?t={second}", environ_base=LAN).status_code == 302   # הקישור החדש מחזיר
    assert len(app.devices()) == 1
    assert c.get("/api/devices", environ_base=LAN).status_code == 200


def test_old_link_is_not_counted_as_a_guess(c):
    first = _tok(app.fresh_link())
    second = _tok(app.fresh_link())
    for _ in range(app.FAIL_LIMIT + 2):
        assert c.get(f"/?t={first}", environ_base=LAN).status_code == 401
    assert c.get(f"/?t={second}", environ_base=LAN).status_code == 302


def test_wrong_token_still_counts_and_blocks(c):
    app.fresh_link()
    for _ in range(app.FAIL_LIMIT):
        assert c.get("/?t=nope", environ_base=LAN).status_code == 401
    assert c.get("/?t=nope", environ_base=LAN).status_code == 429


def test_legacy_env_token_becomes_old(c):
    os.environ["APP_TOKEN"] = "legacy-token"
    try:
        app.fresh_link()
    finally:
        os.environ.pop("APP_TOKEN", None)
    assert app.is_old_token("legacy-token")
    assert c.get("/?t=legacy-token", environ_base=LAN).status_code == 401


def test_tokens_file_keeps_only_hashes_of_old(c):
    first = _tok(app.fresh_link())
    app.fresh_link()
    text = app.TOKENS_FILE.read_text(encoding="utf-8")
    assert first not in text


def test_link_command_sends_fresh_link():
    sent = []
    orig = app.tg_call
    os.environ["TELEGRAM_CHAT_ID"] = "42"
    app.tg_call = lambda method, **kw: sent.append(kw)
    try:
        before = app.TOKEN
        app.tg_handle_message({"chat": {"id": 42}, "text": "/link"})
        assert sent and app.TOKEN != before and app.TOKEN in sent[0]["text"]
        sent.clear()
        app.tg_handle_message({"chat": {"id": 99}, "text": "/link"})     # צ'אט זר
        assert not sent
    finally:
        app.tg_call = orig


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fails = 0
    for t in tests:
        with tempfile.TemporaryDirectory() as d:
            c = _setup(Path(d))
            try:
                t(c) if t.__code__.co_argcount else t()
                print(f"ok   {t.__name__}")
            except AssertionError as exc:
                fails += 1
                print(f"FAIL {t.__name__}: {exc}")
    print(f"{len(tests) - fails}/{len(tests)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())

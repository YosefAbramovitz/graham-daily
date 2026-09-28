"""אישור בטלגרם: שום פקודה בלי לחיצה על אישור, רק מהצ'אט המוגדר, ורק פעם אחת."""
import os

import app

_ORIG = {k: getattr(app, k) for k in ("tg_call", "plan_for", "execute_plan", "swing_close", "api")}


def _restore():
    for k, v in _ORIG.items():
        setattr(app, k, v)
    app._tg["pending"].clear()


def _plan(kind):
    if kind == "graham":
        return {"plan": [{"ticker": "AAA", "qty": 2, "price": 10.0, "target": 15.0, "value": 20.0},
                         {"ticker": "HRB", "qty": 1, "price": 30.0, "target": 45.0, "value": 30.0}],
                "cash": 1000.0}
    if kind == "swing":
        return {"plan": [{"ticker": "HRB", "qty": 3, "price": 30.0, "stop": 28.0, "value": 90.0,
                          "risk": 6}], "cash": 1000.0}
    return {"plan": [], "reason": "אין מזומן"}


def _setup(calls, executed):
    os.environ["TELEGRAM_CHAT_ID"] = "42"
    for pre in ("ALPACA_GRAHAM", "ALPACA_SWING", "ALPACA_SPY"):
        os.environ[f"{pre}_KEY_ID"] = "k"
        os.environ[f"{pre}_SECRET_KEY"] = "s"
    app._tg["pending"].clear()

    def fake_call(method, **kw):
        calls.append((method, kw))
        return {"message_id": 7} if method == "sendMessage" else True
    app.tg_call = fake_call
    app.plan_for = _plan

    def fake_exec(kind, want):
        executed.append((kind, app.current_acct(), set(want)))
        return [{"ticker": t, "qty": 1, "ok": True, "status": "accepted", "error": None}
                for t in sorted(want)]
    app.execute_plan = fake_exec

    def fake_close(sym):
        executed.append(("close", app.current_acct(), {sym}))
        return True, {"status": "accepted"}
    app.swing_close = fake_close


def _cq(data, frm=42):
    return {"id": "c", "from": {"id": frm}, "data": data}


def _run(f):
    calls, executed = [], []
    _setup(calls, executed)
    try:
        f(calls, executed)
    finally:
        _restore()


def test_combined_offer_toggle_approve_once():
    def body(calls, executed):
        app.tg_offer(list(app.PLAN_KINDS), quiet=True)
        tok = next(iter(app._tg["pending"]))
        st = app._tg["pending"][tok]
        assert list(st["parts"]) == ["swing", "graham"]      # spy ריק - לא מוצג בהודעה האוטומטית
        assert "אשר הכל (3)" in str(calls[0][1]["reply_markup"])
        assert not executed
        app.tg_handle_callback(_cq(f"t|{tok}|g|HRB"))       # HRB יורדת רק מגראהם
        assert st["selected"] == {("graham", "AAA"), ("swing", "HRB")}
        app.tg_handle_callback(_cq(f"ok|{tok}"))
        assert ("swing", "swing", {"HRB"}) in executed and ("graham", "graham", {"AAA"}) in executed
        n = len(executed)
        app.tg_handle_callback(_cq(f"ok|{tok}"))              # לחיצה כפולה
        assert len(executed) == n
    _run(body)


def test_other_user_and_cancel_send_nothing():
    def body(calls, executed):
        app.tg_offer("graham")
        tok = next(iter(app._tg["pending"]))
        app.tg_handle_callback(_cq(f"ok|{tok}", frm=99))
        assert not executed and tok in app._tg["pending"]
        app.tg_handle_callback(_cq(f"no|{tok}"))
        app.tg_handle_callback(_cq(f"ok|{tok}"))
        assert not executed
    _run(body)


def test_expired_and_unknown_token():
    def body(calls, executed):
        app.tg_offer("graham")
        tok = next(iter(app._tg["pending"]))
        app._tg["pending"][tok]["at"] -= app.TG_TTL + 1
        app.tg_handle_callback(_cq(f"ok|{tok}"))
        app.tg_handle_callback(_cq("ok|nope"))
        assert not executed
    _run(body)


def test_empty_plan_quiet():
    def body(calls, executed):
        app.tg_offer("spy", quiet=True)
        assert not calls
        app.tg_offer("spy")
        assert calls and "אין מזומן" in calls[0][1]["text"] and not app._tg["pending"]
    _run(body)


def test_sell_day15_needs_click_and_uses_swing_account():
    def body(calls, executed):
        app.tg_offer_sell(["GL", "MS"])
        tok = next(iter(app._tg["pending"]))
        assert not executed
        app.tg_handle_callback(_cq(f"t|{tok}|x|MS"))         # MS נשארת
        app.tg_handle_callback(_cq(f"ok|{tok}"))
        assert executed == [("close", "swing", {"GL"})]
        app.tg_handle_callback(_cq(f"ok|{tok}"))
        assert len(executed) == 1
    _run(body)


def test_commands_only_from_chat():
    def body(calls, executed):
        app.tg_handle_message({"chat": {"id": 99}, "text": "/plan"})
        assert not calls
        app.tg_handle_message({"chat": {"id": 42}, "text": "/graham"})
        assert calls and calls[0][0] == "sendMessage"
    _run(body)


def test_day_summary():
    def body(calls, executed):
        def fake_api(method, url, **kw):
            if url.endswith("/v2/account"):
                return True, {"equity": "10100", "last_equity": "10000", "cash": "500"}
            if url.endswith("/v2/positions"):
                return True, [{"symbol": "GL"}]
            return True, [{"symbol": "GL", "side": "buy", "type": "market", "status": "filled",
                           "filled_at": "2026-09-28T13:31:00Z", "filled_qty": "4",
                           "filled_avg_price": "170", "legs": [
                               {"symbol": "GL", "side": "sell", "type": "stop", "status": "filled",
                                "filled_at": "2026-09-28T18:00:00Z", "filled_avg_price": "161.8"}]}]
        app.api = fake_api
        txt = app.day_summary("2026-09-28")
        assert "+100" in txt and "+1.00%" in txt
        assert "נקנו: GL 4@170.00" in txt and "סטופ הופעל: GL @161.80" in txt
    _run(body)

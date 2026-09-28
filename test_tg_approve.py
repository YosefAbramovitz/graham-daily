"""אישור קנייה בטלגרם: שום פקודה בלי 'אשר', רק מהצ'אט המוגדר, ורק פעם אחת."""
import os

import app

_ORIG = {k: getattr(app, k) for k in ("tg_call", "plan_for", "execute_plan")}


def _restore():
    for k, v in _ORIG.items():
        setattr(app, k, v)
    app._tg["pending"].clear()


PLAN = {"plan": [{"ticker": "AAA", "qty": 2, "price": 10.0, "target": 15.0, "value": 20.0},
                 {"ticker": "BBB", "qty": 1, "price": 30.0, "target": 45.0, "value": 30.0}],
        "cash": 1000.0}


def _setup(monkey_calls, executed):
    os.environ["TELEGRAM_CHAT_ID"] = "42"
    os.environ["ALPACA_GRAHAM_KEY_ID"] = "k"
    os.environ["ALPACA_GRAHAM_SECRET_KEY"] = "s"
    app._tg["pending"].clear()

    def fake_call(method, **kw):
        monkey_calls.append((method, kw))
        return {"message_id": 7} if method == "sendMessage" else True
    app.tg_call = fake_call
    app.plan_for = lambda kind: PLAN

    def fake_exec(kind, want):
        executed.append((kind, app.current_acct(), set(want)))
        return [{"ticker": t, "qty": 1, "ok": True, "status": "accepted", "error": None}
                for t in sorted(want)]
    app.execute_plan = fake_exec


def _cq(data, frm=42):
    return {"id": "c", "from": {"id": frm}, "data": data}


def test_offer_toggle_approve_once():
    try:
        _test_offer_toggle_approve_once()
    finally:
        _restore()


def _test_offer_toggle_approve_once():
    calls, executed = [], []
    _setup(calls, executed)
    app.tg_offer("graham")
    tok = next(iter(app._tg["pending"]))
    assert calls[0][0] == "sendMessage" and "אשר קנייה (2)" in str(calls[0][1]["reply_markup"])
    assert not executed
    app.tg_handle_callback(_cq(f"t|{tok}|BBB"))          # מוריד את BBB
    assert app._tg["pending"][tok]["selected"] == {"AAA"} and not executed
    app.tg_handle_callback(_cq(f"ok|{tok}"))
    assert executed == [("graham", "graham", {"AAA"})]
    app.tg_handle_callback(_cq(f"ok|{tok}"))              # לחיצה כפולה
    assert len(executed) == 1


def test_other_user_and_cancel_send_nothing():
    try:
        _test_other_user_and_cancel_send_nothing()
    finally:
        _restore()


def _test_other_user_and_cancel_send_nothing():
    calls, executed = [], []
    _setup(calls, executed)
    app.tg_offer("graham")
    tok = next(iter(app._tg["pending"]))
    app.tg_handle_callback(_cq(f"ok|{tok}", frm=99))      # לא הצ'אט המוגדר
    assert not executed and tok in app._tg["pending"]
    app.tg_handle_callback(_cq(f"no|{tok}"))
    app.tg_handle_callback(_cq(f"ok|{tok}"))
    assert not executed


def test_expired_and_unknown_token():
    try:
        _test_expired_and_unknown_token()
    finally:
        _restore()


def _test_expired_and_unknown_token():
    calls, executed = [], []
    _setup(calls, executed)
    app.tg_offer("graham")
    tok = next(iter(app._tg["pending"]))
    app._tg["pending"][tok]["at"] -= app.TG_TTL + 1
    app.tg_handle_callback(_cq(f"ok|{tok}"))
    app.tg_handle_callback(_cq("ok|nope"))
    assert not executed


def test_empty_plan_quiet():
    try:
        _test_empty_plan_quiet()
    finally:
        _restore()


def _test_empty_plan_quiet():
    calls, executed = [], []
    _setup(calls, executed)
    app.plan_for = lambda kind: {"plan": [], "reason": "אין מזומן"}
    app.tg_offer("graham", quiet=True)
    assert not calls
    app.tg_offer("graham")
    assert calls and "אין מזומן" in calls[0][1]["text"]


def test_commands_only_from_chat():
    try:
        _test_commands_only_from_chat()
    finally:
        _restore()


def _test_commands_only_from_chat():
    calls, executed = [], []
    _setup(calls, executed)
    app.tg_handle_message({"chat": {"id": 99}, "text": "/plan"})
    assert not calls
    app.tg_handle_message({"chat": {"id": 42}, "text": "/graham"})
    assert calls and calls[0][0] == "sendMessage"

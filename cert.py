"""
תעודת SSL מ-Let's Encrypt לשרת המסך, בלי certbot.

    python cert.py --host graham-daily.myddns.me            # להנפיק או לחדש לפי הצורך
    python cert.py --host graham-daily.myddns.me --staging  # ניסיון מול שרת הבדיקות
    python cert.py --host graham-daily.myddns.me --force    # להנפיק מחדש עכשיו
    python cert.py --status                                  # כמה ימים נשארו

איך זה עובד
-----------
Let's Encrypt בודקת שהשם באמת מצביע אלינו (HTTP-01): היא פונה ל-
http://<host>/.well-known/acme-challenge/<token> בפורט 80. לכן בזמן ההנפקה
עולה כאן שרת זמני על פורט 80, שעונה רק על הכתובת הזאת, ונסגר מיד אחרי.
בראוטר צריך הפניה של פורט 80 חיצוני לפורט 80 במחשב, גם לחידושים.

התעודה תקפה 90 יום. app.py --public מחדש אותה כשנשארו פחות מ-30 יום, בהפעלה
ופעם ביום, וטוען אותה לשרת בלי הפעלה מחדש.

הקבצים ב-certs/ (מחוץ ל-git): account.key, privkey.pem, fullchain.pem.
"""

from __future__ import annotations

import argparse
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Tuple

HERE = Path(__file__).resolve().parent
CERT_DIR = HERE / "certs"
ACCOUNT_KEY = CERT_DIR / "account.key"
PRIVKEY = CERT_DIR / "privkey.pem"
FULLCHAIN = CERT_DIR / "fullchain.pem"
PRODUCTION = "https://acme-v02.api.letsencrypt.org/directory"
STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"
RENEW_DAYS = 30


def days_left(path: Path = FULLCHAIN) -> Optional[int]:
    """כמה ימים נשארו לתעודה, או None אם אין תעודה קריאה."""
    try:
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(path.read_bytes())
    except (OSError, ValueError, ImportError):
        return None
    end = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=timezone.utc)
    return (end - datetime.now(timezone.utc)).days


def cert_host(path: Path = FULLCHAIN) -> Optional[str]:
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        cert = x509.load_pem_x509_certificate(path.read_bytes())
        return cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    except (OSError, ValueError, IndexError, ImportError):
        return None


def _rsa_pem() -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.TraditionalOpenSSL,
                             serialization.NoEncryption())


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


class _Challenge(BaseHTTPRequestHandler):
    tokens: dict = {}

    def do_GET(self):  # noqa: N802
        body = self.tokens.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print("  [80] " + fmt % args)


def obtain(host: str, staging: bool = False, port: int = 80) -> str:
    """מנפיק תעודה חדשה ל-host. זורק חריגה עם הסבר אם נכשל."""
    import josepy as jose
    from acme import challenges, client, crypto_util, errors, messages
    from cryptography.hazmat.primitives import serialization

    CERT_DIR.mkdir(exist_ok=True)
    if not ACCOUNT_KEY.exists():
        _write(ACCOUNT_KEY, _rsa_pem())
    acc_key = jose.JWKRSA(key=serialization.load_pem_private_key(ACCOUNT_KEY.read_bytes(), None))

    net = client.ClientNetwork(acc_key, user_agent="graham-daily")
    directory = client.ClientV2.get_directory(STAGING if staging else PRODUCTION, net)
    acme = client.ClientV2(directory, net=net)
    try:
        acme.new_account(messages.NewRegistration.from_data(terms_of_service_agreed=True))
    except errors.ConflictError as exc:         # החשבון כבר קיים עם המפתח הזה
        acme.query_registration(messages.RegistrationResource(
            uri=exc.location, body=messages.Registration()))

    key_pem = _rsa_pem()
    order = acme.new_order(crypto_util.make_csr(key_pem, [host]))

    _Challenge.tokens = {}
    todo = []
    for authz in order.authorizations:
        chall = next((c for c in authz.body.challenges
                      if isinstance(c.chall, challenges.HTTP01)), None)
        if chall is None:
            raise RuntimeError("Let's Encrypt לא הציעה בדיקת HTTP-01")
        response, validation = chall.response_and_validation(acc_key)
        _Challenge.tokens[chall.chall.path] = validation
        todo.append((chall, response))

    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), _Challenge)
    except OSError as exc:
        raise RuntimeError(f"אי אפשר לפתוח את פורט {port} במחשב ({exc}). "
                           "אולי תוכנה אחרת משתמשת בו.") from exc
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for chall, response in todo:
            acme.answer_challenge(chall, response)
        deadline = datetime.now() + timedelta(seconds=120)
        try:
            done = acme.poll_and_finalize(order, deadline=deadline)
        except errors.ValidationError as exc:
            detail = "; ".join(str(a.body.challenges[0].error or "")
                               for a in exc.failed_authzrs) if getattr(exc, "failed_authzrs", None) else str(exc)
            raise RuntimeError(
                "Let's Encrypt לא הצליחה להגיע למחשב בפורט 80. בדוק שבראוטר יש הפניה של "
                f"פורט 80 למחשב, ושהשם {host} מצביע על הכתובת של הבית. פירוט: {detail}") from exc
    finally:
        server.shutdown()
        server.server_close()

    _write(PRIVKEY, key_pem)
    _write(FULLCHAIN, done.fullchain_pem.encode())
    return f"הונפקה תעודה ל-{host}{' (staging)' if staging else ''}, תקפה עוד {days_left()} ימים"


def ensure(host: str, staging: bool = False, force: bool = False) -> Tuple[bool, str]:
    """מנפיק רק אם אין תעודה, השם שונה, או שנשארו פחות מ-RENEW_DAYS ימים."""
    left = days_left()
    if not force and left is not None and left >= RENEW_DAYS and cert_host() == host:
        return False, f"התעודה ל-{host} בתוקף עוד {left} ימים"
    return True, obtain(host, staging=staging)


def main() -> int:
    ap = argparse.ArgumentParser(description="תעודת Let's Encrypt לשרת המסך")
    ap.add_argument("--host")
    ap.add_argument("--staging", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status or not args.host:
        print(f"תעודה: {cert_host() or 'אין'}, ימים שנשארו: {days_left()}")
        return 0
    try:
        changed, msg = ensure(args.host, staging=args.staging, force=args.force)
    except Exception as exc:  # הודעה ברורה במקום traceback
        print(f"נכשל: {type(exc).__name__}: {exc}")
        return 1
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())

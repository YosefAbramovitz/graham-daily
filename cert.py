"""
תעודת SSL לשרת המסך. שני מצבים (CERT_MODE ב-.env):

  private (ברירת המחדל) - CA פרטי שיוצרים כאן, מוגבל לחתום רק על השם של השרת
      (Name Constraints), ותעודת שרת ממנו. מתקינים את ה-CA פעם אחת בכל טלפון
      (certs/graham-ca.crt, נשלח גם לטלגרם ונגיש ב-/ca.crt), ואז אין אזהרות.
      לא צריך פורט 80 ולא שירות חיצוני.
  letsencrypt - תעודה מ-Let's Encrypt (למטה). צריך פורט 80 פתוח בראוטר.

    python cert.py --host graham-daily.myddns.me --private   # CA פרטי

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
CA_KEY = CERT_DIR / "ca.key"
CA_CERT = CERT_DIR / "ca.pem"
CA_EXPORT = CERT_DIR / "graham-ca.crt"      # DER, להתקנה בטלפון
LEAF_DAYS = 800                              # iOS מקבל עד 825 יום מ-CA פרטי


def days_left(path: Optional[Path] = None) -> Optional[int]:
    """כמה ימים נשארו לתעודה, או None אם אין תעודה קריאה."""
    try:
        from cryptography import x509
        cert = x509.load_pem_x509_certificate((path or FULLCHAIN).read_bytes())
    except (OSError, ValueError, ImportError):
        return None
    end = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=timezone.utc)
    return (end - datetime.now(timezone.utc)).days


def cert_host(path: Optional[Path] = None) -> Optional[str]:
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        cert = x509.load_pem_x509_certificate((path or FULLCHAIN).read_bytes())
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


def _ec_key():
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec.generate_private_key(ec.SECP256R1())


def _key_pem(key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def ensure_ca(host: str) -> bool:
    """יוצר CA פרטי אם אין (או אם הוא לשם אחר). מחזיר True אם נוצר חדש."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import NameOID

    if CA_KEY.exists() and CA_CERT.exists():
        try:
            ca = x509.load_pem_x509_certificate(CA_CERT.read_bytes())
            nc = ca.extensions.get_extension_for_class(x509.NameConstraints).value
            if any(n.value == host for n in (nc.permitted_subtrees or [])):
                return False
        except (ValueError, x509.ExtensionNotFound):
            pass
    CERT_DIR.mkdir(exist_ok=True)
    key = _ec_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"graham-daily private CA ({host})"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "graham-daily")])
    now = datetime.now(timezone.utc)
    ca = (x509.CertificateBuilder()
          .subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=5))
          .not_valid_after(now + timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          # ה-CA יכול לחתום רק על השם של השרת. גם אם המפתח שלו ידלוף, אי אפשר
          # להשתמש בו כדי להתחזות לאתר אחר בטלפון שהתקין אותו.
          .add_extension(x509.NameConstraints(permitted_subtrees=[x509.DNSName(host)],
                                              excluded_subtrees=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
          .sign(key, hashes.SHA256()))
    _write(CA_KEY, _key_pem(key))
    _write(CA_CERT, ca.public_bytes(serialization.Encoding.PEM))
    _write(CA_EXPORT, ca.public_bytes(serialization.Encoding.DER))
    return True


def issue_private(host: str) -> str:
    """תעודת שרת ל-host, חתומה ב-CA הפרטי."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ca_key = serialization.load_pem_private_key(CA_KEY.read_bytes(), None)
    ca = x509.load_pem_x509_certificate(CA_CERT.read_bytes())
    key = _ec_key()
    now = datetime.now(timezone.utc)
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(ca.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=LEAF_DAYS))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=False, crl_sign=False,
                                         content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                           critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write(PRIVKEY, _key_pem(key))
    _write(FULLCHAIN, leaf.public_bytes(serialization.Encoding.PEM) +
           ca.public_bytes(serialization.Encoding.PEM))
    return f"הונפקה תעודה פרטית ל-{host}, תקפה עוד {days_left()} ימים"


def ensure(host: str, staging: bool = False, force: bool = False,
           mode: str = "private") -> Tuple[bool, str]:
    """מנפיק רק אם אין תעודה, השם שונה, ה-CA הוחלף, או שנשארו פחות מ-RENEW_DAYS ימים."""
    new_ca = ensure_ca(host) if mode == "private" else False
    left = days_left()
    if (not force and not new_ca and left is not None and left >= RENEW_DAYS
            and cert_host() == host and is_private() == (mode == "private")):
        return False, f"התעודה ל-{host} בתוקף עוד {left} ימים"
    if mode == "private":
        return True, issue_private(host) + (" (נוצר CA חדש: צריך להתקין אותו בטלפון)" if new_ca else "")
    return True, obtain(host, staging=staging)


def is_private() -> bool:
    """האם התעודה הנוכחית חתומה ב-CA הפרטי."""
    try:
        from cryptography import x509
        leaf = x509.load_pem_x509_certificate(FULLCHAIN.read_bytes())
        return CA_CERT.exists() and leaf.issuer == x509.load_pem_x509_certificate(CA_CERT.read_bytes()).subject
    except (OSError, ValueError, ImportError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="תעודת Let's Encrypt לשרת המסך")
    ap.add_argument("--host")
    ap.add_argument("--staging", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--private", action="store_true", help="CA פרטי במקום Let's Encrypt")
    args = ap.parse_args()
    if args.status or not args.host:
        print(f"תעודה: {cert_host() or 'אין'}, ימים שנשארו: {days_left()}")
        return 0
    try:
        changed, msg = ensure(args.host, staging=args.staging, force=args.force,
                              mode="private" if args.private else "letsencrypt")
    except Exception as exc:  # הודעה ברורה במקום traceback
        print(f"נכשל: {type(exc).__name__}: {exc}")
        return 1
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())

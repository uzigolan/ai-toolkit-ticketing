"""
Generate a self-signed certificate for local HTTPS testing.

    python scripts/make_self_signed_cert.py

Writes the cert and key named in config.ini's [HTTPS] section, under https/.
Browsers will warn -- it is not signed by any CA. Use it to check the HTTPS
path works, then replace it with a real certificate (PKI Squire issues these).
"""
import configparser
import ipaddress
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
except ImportError:
    sys.exit("This helper needs the cryptography package: pip install cryptography")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CERT = "https/tls.cert.pem"
DEFAULT_KEY = "https/tls.key.pem"
DAYS_VALID = 825  # the maximum most browsers accept for a leaf certificate


def target_paths() -> tuple:
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(REPO_ROOT, "config.ini"))
    cert = cfg.get("HTTPS", "ssl_cert", fallback=DEFAULT_CERT).strip() or DEFAULT_CERT
    key = cfg.get("HTTPS", "ssl_key", fallback=DEFAULT_KEY).strip() or DEFAULT_KEY
    return (os.path.join(REPO_ROOT, cert), os.path.join(REPO_ROOT, key))


def main():
    cert_path, key_path = target_paths()
    if os.path.exists(cert_path) and "--force" not in sys.argv:
        sys.exit(f"{cert_path} already exists. Re-run with --force to replace it.")

    os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    os.makedirs(os.path.dirname(key_path), exist_ok=True)

    hostname = os.environ.get("TICKETING_HOSTNAME", "localhost")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "RAD AI Ticketing Center"),
    ])
    names = [hostname, "localhost"] if hostname != "localhost" else ["localhost"]
    alt_names = [x509.DNSName(n) for n in names]
    alt_names.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))

    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=DAYS_VALID))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=True, key_encipherment=True,
                          content_commitment=False, data_encipherment=False,
                          key_agreement=False, key_cert_sign=False, crl_sign=False,
                          encipher_only=False, decipher_only=False),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.ObjectIdentifier("1.3.6.1.5.5.7.3.1")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    with open(cert_path, "wb") as handle:
        handle.write(certificate.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as handle:
        handle.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
    # The key is readable only by its owner where the OS supports it.
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass

    print(f"cert: {cert_path}")
    print(f"key : {key_path}")
    print(f"CN={hostname}, valid {DAYS_VALID} days, SAN: {', '.join(names)}, 127.0.0.1")
    print("Set [HTTPS] enabled = true in config.ini, then start the app.")


if __name__ == "__main__":
    main()

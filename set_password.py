#!/usr/bin/env python3
"""Setzt APP_PASSWORD_HASH in .env. Nur Standardbibliothek, läuft direkt auf dem Host.

Erzeugt einen werkzeug-kompatiblen scrypt-Hash (scrypt:32768:8:1$salz$hash).
Aufruf im Projektverzeichnis:  python3 set_password.py
"""

import getpass
import hashlib
import os
import secrets
import string
import sys
from pathlib import Path

N, R, P = 32768, 8, 1
ENV = Path(__file__).resolve().parent / ".env"


def make_hash(password):
    salt = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    dk = hashlib.scrypt(password.encode(), salt=salt.encode(), n=N, r=R, p=P, maxmem=132 * N * R * P, dklen=64)
    return f"scrypt:{N}:{R}:{P}${salt}${dk.hex()}"


def main():
    if not ENV.exists():
        sys.exit(f"{ENV} nicht gefunden")
    pw = getpass.getpass("Neues Passwort (min. 16 Zeichen): ")
    if len(pw) < 16:
        sys.exit("Abbruch: weniger als 16 Zeichen.")
    if pw != getpass.getpass("Wiederholen: "):
        sys.exit("Abbruch: Passwörter stimmen nicht überein.")

    line = f"APP_PASSWORD_HASH='{make_hash(pw)}'\n"
    lines = ENV.read_text().splitlines(keepends=True)
    out, done = [], False
    for ln in lines:
        if ln.startswith("APP_PASSWORD_HASH="):
            if not done:
                out.append(line)
                done = True
        else:
            out.append(ln)
    if not done:
        out.append(line)

    tmp = ENV.with_name(".env.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.writelines(out)
    os.replace(tmp, ENV)
    print("Passwort gesetzt. Jetzt: docker compose up -d")


if __name__ == "__main__":
    main()

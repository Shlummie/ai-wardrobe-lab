from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import qrcode

from . import settings


class MobileSessionError(RuntimeError):
    pass


class MobileSessionExpired(MobileSessionError):
    pass


class MobileAuthenticationError(MobileSessionError):
    pass


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_loopback_client(host: str | None) -> bool:
    if not host:
        return False
    if host == "testclient":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def discover_private_ipv4() -> str:
    candidates: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            candidates.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.append(result[4][0])
    except OSError:
        pass
    for candidate in dict.fromkeys(candidates):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version == 4 and address.is_private and not address.is_loopback:
            return candidate
    raise MobileSessionError(
        "No private IPv4 address was found. Connect the PC and iPhone to the same Wi-Fi network."
    )


@dataclass
class MobileSession:
    session_id: str
    join_digest: str
    pin: str
    created_at: float
    last_activity: float
    hard_expires_at: float
    lan_ip: str
    mobile_url: str
    qr_path: Path
    failed_pin_attempts: int = 0
    access_digests: set[str] = field(default_factory=set)
    uploads: int = 0
    reference_uploaded: bool = False


class MobileSessionManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._session: MobileSession | None = None

    def _deadline(self, session: MobileSession) -> float:
        return min(
            session.last_activity + settings.MOBILE_IDLE_SECONDS,
            session.hard_expires_at,
        )

    def _discard_locked(self) -> None:
        if self._session:
            self._session.qr_path.unlink(missing_ok=True)
        self._session = None

    def _active_locked(self) -> MobileSession | None:
        if self._session and time.time() >= self._deadline(self._session):
            self._discard_locked()
        return self._session

    def _public_info(self, session: MobileSession, *, include_secret: bool) -> dict:
        deadline = self._deadline(session)
        info = {
            "active": True,
            "id": session.session_id,
            "lan_ip": session.lan_ip,
            "mobile_url": session.mobile_url if include_secret else None,
            "qr_path": str(session.qr_path) if include_secret else None,
            "pin": session.pin if include_secret else None,
            "created_at": datetime.fromtimestamp(
                session.created_at, tz=timezone.utc
            ).isoformat(),
            "expires_at": datetime.fromtimestamp(deadline, tz=timezone.utc).isoformat(),
            "seconds_remaining": max(0, int(deadline - time.time())),
            "uploads": session.uploads,
            "reference_uploaded": session.reference_uploaded,
        }
        return info

    def create(self, lan_ip: str | None = None) -> dict:
        with self._lock:
            self._discard_locked()
            address = lan_ip or discover_private_ipv4()
            join_token = secrets.token_urlsafe(32)
            pin = f"{secrets.randbelow(1_000_000):06d}"
            session_id = secrets.token_hex(8)
            mobile_url = (
                f"http://{address}:{settings.MOBILE_WEB_PORT}/mobile"
                f"#join={quote(join_token, safe='')}"
            )
            qr_path = settings.CACHE_DIR / f"mobile-intake-{session_id}.png"
            qr_path.parent.mkdir(parents=True, exist_ok=True)
            code = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=9,
                border=3,
            )
            code.add_data(mobile_url)
            code.make(fit=True)
            code.make_image(fill_color="#1d1c1a", back_color="#fbfaf6").save(qr_path)
            now = time.time()
            self._session = MobileSession(
                session_id=session_id,
                join_digest=_digest(join_token),
                pin=pin,
                created_at=now,
                last_activity=now,
                hard_expires_at=now + settings.MOBILE_MAX_SECONDS,
                lan_ip=address,
                mobile_url=mobile_url,
                qr_path=qr_path,
            )
            return self._public_info(self._session, include_secret=True)

    def current(self, *, include_secret: bool) -> dict:
        with self._lock:
            session = self._active_locked()
            if not session:
                return {"active": False}
            return self._public_info(session, include_secret=include_secret)

    def stop(self) -> None:
        with self._lock:
            self._discard_locked()

    def authenticate(self, join_token: str, pin: str) -> tuple[str, dict]:
        with self._lock:
            session = self._active_locked()
            if not session:
                raise MobileSessionExpired("The mobile intake session has expired.")
            if not hmac.compare_digest(session.join_digest, _digest(join_token)):
                raise MobileAuthenticationError("The mobile intake link is invalid.")
            if session.failed_pin_attempts >= 8:
                self._discard_locked()
                raise MobileAuthenticationError(
                    "Too many incorrect PIN attempts. Start a new session on the PC."
                )
            if not hmac.compare_digest(session.pin, pin.strip()):
                session.failed_pin_attempts += 1
                if session.failed_pin_attempts >= 8:
                    self._discard_locked()
                raise MobileAuthenticationError("The PIN is incorrect.")
            access_token = secrets.token_urlsafe(36)
            session.access_digests.add(_digest(access_token))
            session.last_activity = time.time()
            return access_token, self._public_info(session, include_secret=False)

    def validate_access(self, access_token: str, *, touch: bool = True) -> MobileSession:
        with self._lock:
            session = self._active_locked()
            if not session:
                raise MobileSessionExpired("The mobile intake session has expired.")
            candidate = _digest(access_token)
            if not any(
                hmac.compare_digest(candidate, allowed)
                for allowed in session.access_digests
            ):
                raise MobileAuthenticationError("Mobile authorization is invalid.")
            if touch:
                session.last_activity = time.time()
            return session

    def record_upload(self, access_token: str, *, reference: bool = False) -> dict:
        with self._lock:
            session = self.validate_access(access_token)
            if reference:
                session.reference_uploaded = True
            else:
                session.uploads += 1
            return self._public_info(session, include_secret=False)

    def finish(self, access_token: str) -> None:
        with self._lock:
            self.validate_access(access_token, touch=False)
            self._discard_locked()


mobile_sessions = MobileSessionManager()

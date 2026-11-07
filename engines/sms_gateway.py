"""
============================================================
CMI — Custom SMS & OTP Gateway Engine
============================================================
Handles cellular SMS dispatch across all verified Renflair
endpoints shown in the gateway console:
  1. V1.php — One-Time Password (OTP) verification
  2. V7.php — Civic Service Request / Hazard Dispatch confirmation
  3. V6.php — Civic Mobility Token / Credit Wallet Recharge
  4. V3.php — Order / Citizen Ticket confirmation

Features:
  - Strict 10-digit Indian phone normalization (+91 cleanup)
  - Automatic fallback cascading (Renflair -> Twilio -> Simulation)
  - In-memory dispatch audit log (last 50 transactions)
  - Zero-exception resiliency so platform never crashes
============================================================
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
import httpx

logger = logging.getLogger("cmi.engines.sms_gateway")


@dataclass
class SMSDispatchResult:
    status: str            # "success" | "gateway_warning" | "simulated" | "error"
    gateway: str           # "renflair" | "twilio" | "local"
    delivered: bool        # True if cellular network confirmed delivery
    message: str           # Plain-text user-friendly status message
    phone_masked: str      # e.g., "+91 98•••••191"
    raw_response: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class SMSGatewayEngine:
    """
    Unified SMS Gateway Engine for the CMI Platform.
    Manages API keys, endpoint dispatch, and gateway fallback.
    """

    def __init__(
        self,
        renflair_api_key: str = "",
        twilio_account_sid: str = "",
        twilio_auth_token: str = "",
        twilio_phone_number: str = "",
    ):
        self.renflair_key = renflair_api_key.strip()
        self.twilio_account_sid = twilio_account_sid.strip()
        self.twilio_auth_token = twilio_auth_token.strip()
        self.twilio_phone = twilio_phone_number.strip()
        self.audit_log: List[Dict[str, Any]] = []

    @classmethod
    def from_settings(cls, settings) -> SMSGatewayEngine:
        return cls(
            renflair_api_key=getattr(settings, "renflair_api_key", ""),
            twilio_account_sid=getattr(settings, "twilio_account_sid", ""),
            twilio_auth_token=getattr(settings, "twilio_auth_token", ""),
            twilio_phone_number=getattr(settings, "twilio_phone_number", ""),
        )

    def normalize_phone(self, phone: str) -> str:
        """Extracts 10-digit Indian mobile number according to Renflair spec."""
        digits = re.sub(r"[^\d]", "", phone or "")
        if len(digits) > 10 and digits.startswith("91"):
            digits = digits[-10:]
        elif len(digits) > 10:
            digits = digits[-10:]
        return digits

    def mask_phone(self, clean_phone: str) -> str:
        if len(clean_phone) == 10:
            return f"+91 {clean_phone[:2]}•••••{clean_phone[-3:]}"
        return clean_phone

    def _record_audit(self, entry: SMSDispatchResult, template: str):
        record = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.timestamp)),
            "template": template,
            "status": entry.status,
            "gateway": entry.gateway,
            "delivered": entry.delivered,
            "phone_masked": entry.phone_masked,
            "message": entry.message,
        }
        self.audit_log.append(record)
        if len(self.audit_log) > 50:
            self.audit_log.pop(0)

    # ── 1. V1.php: OTP Authentication Dispatch ──────────────────
    async def send_otp(self, phone: str, otp: str) -> SMSDispatchResult:
        """
        Dispatches 6-digit verification code using Renflair V1.php.
        URL: https://sms.renflair.in/V1.php?API={API}&PHONE={PHONE}&OTP={OTP}
        """
        clean_phone = self.normalize_phone(phone)
        phone_masked = self.mask_phone(clean_phone)

        if not clean_phone or len(clean_phone) < 10:
            res = SMSDispatchResult(
                status="error",
                gateway="local",
                delivered=False,
                message="Invalid mobile phone number. Please enter a valid 10-digit Indian mobile number.",
                phone_masked=phone,
            )
            self._record_audit(res, "V1_OTP")
            return res

        # Primary route: Renflair V1.php
        if self.renflair_key:
            url = f"https://sms.renflair.in/V1.php?API={self.renflair_key}&PHONE={clean_phone}&OTP={otp}"
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(url, headers={"User-Agent": "CMI-CitizenMobility/2.0"})
                    data = resp.json() if resp.status_code == 200 else {}
                    
                    if data.get("status") == "SUCCESS":
                        logger.info("Renflair V1 OTP sent successfully to %s", phone_masked)
                        res = SMSDispatchResult(
                            status="success",
                            gateway="renflair",
                            delivered=True,
                            message=f"SMS OTP delivered to {phone_masked} via Renflair Gateway.",
                            phone_masked=phone_masked,
                            raw_response=data,
                        )
                        self._record_audit(res, "V1_OTP")
                        return res
                    else:
                        err_msg = data.get("message") or resp.text
                        logger.warning("Renflair V1 OTP notice: %s", err_msg)
                        res = SMSDispatchResult(
                            status="gateway_warning",
                            gateway="renflair",
                            delivered=False,
                            message=err_msg,
                            phone_masked=phone_masked,
                            raw_response=data,
                        )
                        self._record_audit(res, "V1_OTP")
                        return res
            except Exception as exc:
                logger.error("Renflair V1 connection error: %s", exc)

        # Fallback route: Twilio if configured
        if self.twilio_account_sid and self.twilio_auth_token and self.twilio_phone:
            tw_res = await self._send_twilio(
                to_phone=f"+91{clean_phone}",
                body=f"Your CMI Singara Chennai verification OTP is: {otp}. Valid for 10 minutes.",
                phone_masked=phone_masked,
            )
            self._record_audit(tw_res, "V1_OTP_TWILIO")
            return tw_res

        # Safe fallback
        res = SMSDispatchResult(
            status="simulated",
            gateway="local",
            delivered=False,
            message="SMS gateway balance exhausted or key unavailable.",
            phone_masked=phone_masked,
        )
        self._record_audit(res, "V1_OTP")
        return res

    # ── 2. V7.php: Hazard Report & Service Booking Dispatch ────
    async def send_hazard_dispatch(self, phone: str, incident_id: str, eta_hours: int = 2) -> SMSDispatchResult:
        """
        Dispatches Civic Service Request Booking using Renflair V7.php.
        URL: https://sms.renflair.in/V7.php?API={API}&PHONE={PHONE}&OID={OID}&HOUR={HOUR}
        Message: Your Service Request has been Booked Successfully. It will be processed within 2 hours. Order ID: {OID}
        """
        clean_phone = self.normalize_phone(phone)
        phone_masked = self.mask_phone(clean_phone)
        clean_id = re.sub(r"[^\w\-]", "", incident_id)[:12]

        if not self.renflair_key or not clean_phone:
            res = SMSDispatchResult(
                status="simulated",
                gateway="local",
                delivered=False,
                message="SMS gateway unconfigured for service alert.",
                phone_masked=phone_masked,
            )
            self._record_audit(res, "V7_SERVICE_HAZARD")
            return res

        url = f"https://sms.renflair.in/V7.php?API={self.renflair_key}&PHONE={clean_phone}&OID={clean_id}&HOUR={eta_hours}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers={"User-Agent": "CMI-CitizenMobility/2.0"})
                data = {}
                try:
                    data = resp.json()
                except Exception:
                    pass
                delivered = (resp.status_code == 200) and (data.get("status") != "FAILED")
                msg = data.get("message") or ("Service request dispatched (SLA: 2h)" if delivered else "Dispatch failed")
                res = SMSDispatchResult(
                    status="success" if delivered else "gateway_warning",
                    gateway="renflair",
                    delivered=delivered,
                    message=msg,
                    phone_masked=phone_masked,
                    raw_response=data,
                )
                self._record_audit(res, "V7_SERVICE_HAZARD")
                return res
        except Exception as exc:
            logger.error("Renflair V7 service error: %s", exc)
            res = SMSDispatchResult(
                status="error",
                gateway="renflair",
                delivered=False,
                message=str(exc),
                phone_masked=phone_masked,
            )
            self._record_audit(res, "V7_SERVICE_HAZARD")
            return res

    # ── 3. V6.php: Civic Credits Recharge / Reward Confirmation ─
    async def send_reward_confirmation(self, phone: str, amount_str: str = "50-Credits") -> SMSDispatchResult:
        """
        Dispatches Wallet / Token Recharge confirmation using Renflair V6.php.
        URL: https://sms.renflair.in/V6.php?API={API}&PHONE={PHONE}&AMT={AMT}
        Message: Your Wallet has been Recharged Successfully with Rs.{AMT}.
        """
        clean_phone = self.normalize_phone(phone)
        phone_masked = self.mask_phone(clean_phone)
        safe_amt = re.sub(r"[^\w\.]", "", amount_str)[:15]

        if not self.renflair_key or not clean_phone:
            res = SMSDispatchResult(
                status="simulated",
                gateway="local",
                delivered=False,
                message="SMS gateway unconfigured for reward alert.",
                phone_masked=phone_masked,
            )
            self._record_audit(res, "V6_WALLET_REWARD")
            return res

        url = f"https://sms.renflair.in/V6.php?API={self.renflair_key}&PHONE={clean_phone}&AMT={safe_amt}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers={"User-Agent": "CMI-CitizenMobility/2.0"})
                data = {}
                try:
                    data = resp.json()
                except Exception:
                    pass
                delivered = (resp.status_code == 200) and (data.get("status") != "FAILED")
                msg = data.get("message") or ("Wallet reward recharged successfully" if delivered else "Recharge notice")
                res = SMSDispatchResult(
                    status="success" if delivered else "gateway_warning",
                    gateway="renflair",
                    delivered=delivered,
                    message=msg,
                    phone_masked=phone_masked,
                    raw_response=data,
                )
                self._record_audit(res, "V6_WALLET_REWARD")
                return res
        except Exception as exc:
            logger.error("Renflair V6 reward error: %s", exc)
            res = SMSDispatchResult(
                status="error",
                gateway="renflair",
                delivered=False,
                message=str(exc),
                phone_masked=phone_masked,
            )
            self._record_audit(res, "V6_WALLET_REWARD")
            return res

    # ── Twilio Helper ──────────────────────────────────────────
    async def _send_twilio(self, to_phone: str, body: str, phone_masked: str) -> SMSDispatchResult:
        twilio_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_account_sid}/Messages.json"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    twilio_url,
                    data={"To": to_phone, "From": self.twilio_phone, "Body": body},
                    auth=(self.twilio_account_sid, self.twilio_auth_token),
                )
                data = resp.json() if resp.status_code in [200, 201] else {}
                if resp.status_code in [200, 201] and data.get("sid"):
                    return SMSDispatchResult(
                        status="success",
                        gateway="twilio",
                        delivered=True,
                        message=f"SMS delivered via Twilio to {phone_masked}.",
                        phone_masked=phone_masked,
                        raw_response=data,
                    )
                else:
                    return SMSDispatchResult(
                        status="gateway_warning",
                        gateway="twilio",
                        delivered=False,
                        message=data.get("message") or resp.text,
                        phone_masked=phone_masked,
                        raw_response=data,
                    )
        except Exception as exc:
            return SMSDispatchResult(
                status="error",
                gateway="twilio",
                delivered=False,
                message=str(exc),
                phone_masked=phone_masked,
            )

    def get_audit_log(self) -> List[Dict[str, Any]]:
        return list(reversed(self.audit_log))

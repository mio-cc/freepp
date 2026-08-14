# -*- coding: utf-8 -*-
"""Pure-protocol guest-to-member identity-elevation mode.

Ported from paypal-agreement-protocol `paypal/elevation_flow.py` and adapted
to the min-implant ba_paypal codebase (no Chromium / no manual steps).

The base PayPalFlow remains untouched and selectable.  This variant adds a
strict EC-token gate plus a post-signup identity hydration pass before the
existing authorize phase, which solves the historic blocker where
SignUpNewMember succeeded but the member identity was never attached to the
EC checkout (authorize -> BUYER_NOT_SET / EUAT not finalized).
"""
from __future__ import annotations

import json
import urllib.parse
from types import SimpleNamespace
from typing import Any

from loguru import logger

from paypal.flow import PayPalFlow
from paypal.graphql import (
    BUYER_CONTEXT_QUERY,
    BUYER_FUNDING_CONTEXT_QUERY,
    CHECKOUT_SESSION_DATA_QUERY,
)
from paypal.session import sanitize_for_log


class IdentityElevationPayPalFlow(PayPalFlow):
    """PayPal protocol flow with an explicit guest-to-member hydration step."""

    _strict_buyer_context = True

    def _load_hagrid_review_context(
        self,
        hermes_base_url: str,
        hermes_contingency_url: str,
        review_referer: str,
    ) -> bool:
        # Once the AE BuyerContext has been hydrated, reloading Signup/Hermes
        # can downgrade the same checkout back to ANONYMOUS. Keep the
        # established member session instead of navigating again.
        if (
            str(getattr(self.address, "country", "") or "").upper() in {"AE", "BA"}
            and getattr(self, "_ae_pre_hydrated", False)
        ):
            logger.info(
                "AE/BA pre-hydrated member session preserved; skipping destructive review navigation"
            )
            return True

        # AE checkout pages may clear the newly-issued EUAT cookie before the
        # member BuyerContext query runs. Preserve the signup token across the
        # HTML redirect chain; a later GraphQL buyer response may still refresh it.
        saved_euat = str(getattr(self.state, "euat_token", "") or "")
        result = super()._load_hagrid_review_context(
            hermes_base_url,
            hermes_contingency_url,
            review_referer,
        )
        if (
            str(getattr(self.address, "country", "") or "").upper() in {"AE", "BA"}
            and saved_euat
            and not self.state.euat_token
        ):
            logger.info("AE/BA review navigation cleared EUAT; restoring signup access token")
            self.state.euat_token = saved_euat
            self._ensure_euat_cookie()
        return result

    @staticmethod
    def _require_checkout_session(result) -> dict:
        item = result[0] if isinstance(result, list) and result else result
        if not isinstance(item, dict):
            raise RuntimeError("IDENTITY_ELEVATION_CONTEXT_INVALID: checkout response type")
        if item.get("errors"):
            detail = ", ".join(
                str(error.get("message") or error.get("name") or "unknown error")
                for error in item.get("errors") or []
                if isinstance(error, dict)
            )
            raise RuntimeError(
                "IDENTITY_ELEVATION_CONTEXT_REJECTED: " + (detail or "GraphQL error")
            )
        checkout = (item.get("data") or {}).get("checkoutSession")
        if not isinstance(checkout, dict):
            raise RuntimeError("IDENTITY_ELEVATION_CONTEXT_MISSING: checkoutSession")
        if checkout.get("checkoutSessionType") != "BILLING_WITHOUT_PURCHASE":
            raise RuntimeError("IDENTITY_ELEVATION_CONTEXT_TYPE_MISMATCH")
        return checkout

    def _phase2_create_account(self):
        result = super()._phase2_create_account()
        if not self.state.ec_token or not self._is_ec_token(self.state.ec_token):
            raise RuntimeError(
                "IDENTITY_ELEVATION_EC_MISSING: onboarding did not produce an EC checkout token"
            )
        checkout_result = self.session.graphql(
            "CheckoutSessionDataQuery",
            CHECKOUT_SESSION_DATA_QUERY,
            {"token": self.state.ec_token},
        )
        self._require_checkout_session(checkout_result)
        if not self.state.signup_url or not self.state.content_identifier:
            raise RuntimeError("IDENTITY_ELEVATION_SIGNUP_CONTEXT_MISSING")
        self.state.signup_context_ready = True
        logger.info("Identity-elevation EC/signup context validated")
        return result

    def _phase3_signup_and_2fa(self):
        if not self.state.ec_token or not self._is_ec_token(self.state.ec_token):
            raise RuntimeError("IDENTITY_ELEVATION_EC_MISSING: signup requires an EC checkout token")
        if not getattr(self.state, "signup_context_ready", False):
            raise RuntimeError("IDENTITY_ELEVATION_SIGNUP_CONTEXT_NOT_READY")
        return super()._phase3_signup_and_2fa()

    @staticmethod
    def _funding_summary(checkout: dict) -> dict:
        options = checkout.get("fundingOptions")
        options = options if isinstance(options, dict) else {}
        selected = options.get("fundingInstrument")
        selected = selected if isinstance(selected, dict) else {}
        available_ids: set[str] = set()
        for plan in options.get("allPlans") or []:
            if not isinstance(plan, dict):
                continue
            for source in plan.get("fundingSources") or []:
                if not isinstance(source, dict):
                    continue
                instrument = source.get("fundingInstrument")
                instrument = instrument if isinstance(instrument, dict) else {}
                instrument_id = str(instrument.get("id") or "")
                if instrument_id:
                    available_ids.add(instrument_id)
        return {
            "selected": bool(selected.get("id")),
            "available": bool(available_ids),
            "available_count": len(available_ids),
        }

    def _query_elevated_context(self, token: str, referer: str) -> dict:
        headers = {
            "Referer": referer,
            "X-App-Name": "checkoutuinodeweb",
            "X-PayPal-Internal-EUAT": self.state.euat_token,
            "PayPal-Client-Context": token,
            "PayPal-Client-Metadata-Id": self.state.paypal_client_metadata_id,
            "X-Country": self._profile_country(),
            "X-Locale": self._profile_locale(),
        }
        result = self.session.graphql(
            "BuyerFundingContextQuery",
            BUYER_FUNDING_CONTEXT_QUERY,
            {"token": token},
            extra_headers=headers,
            endpoint="https://www.paypal.com/graphql/",
        )
        funding_obj = result[0] if isinstance(result, list) and result else result
        funding_obj = funding_obj if isinstance(funding_obj, dict) else {}
        funding_errors = [
            item for item in (funding_obj.get("errors") or [])
            if isinstance(item, dict)
        ]
        funding_error_messages = [
            str(item.get("message") or item.get("name") or "").strip()
            for item in funding_errors
            if str(item.get("message") or item.get("name") or "").strip()
        ]
        funding_checkpoints = sorted({
            str(checkpoint)
            for item in funding_errors
            for checkpoint in (item.get("checkpoints") or [])
            if str(checkpoint)
        })
        fatal_messages = {
            "PAYER_ACCOUNT_RESTRICTED",
            "PAYER_INVALID_FOR_PAYMENT",
            "TRANSACTION_REFUSED",
            "ACCOUNT_RESTRICTED",
            "ACCOUNT_LOCKED",
        }
        fatal_contingency = next(
            (message for message in funding_error_messages if message in fatal_messages),
            "",
        )

        def checkout_from(payload: dict) -> dict:
            data = payload.get("data")
            data = data if isinstance(data, dict) else {}
            checkout = data.get("checkoutSession")
            return checkout if isinstance(checkout, dict) else {}

        checkout = checkout_from(funding_obj)
        buyer = checkout.get("buyer")
        buyer = buyer if isinstance(buyer, dict) else {}

        # Funding queries commonly return partial buyer data together with a
        # recoverable NEED_CREDIT_CARD contingency. Preserve that buyer instead
        # of discarding the whole response. Fall back only when buyer identity
        # is genuinely absent.
        if not str(buyer.get("userId") or ""):
            logger.warning(
                "Identity-elevation funding query requires buyer-only fallback: {}",
                json.dumps(sanitize_for_log(funding_obj), ensure_ascii=False)[:1000],
            )
            fallback = self.session.graphql(
                "BuyerContextQuery",
                BUYER_CONTEXT_QUERY,
                {"token": token},
                extra_headers=headers,
                endpoint="https://www.paypal.com/graphql/",
            )
            fallback_obj = fallback[0] if isinstance(fallback, list) and fallback else fallback
            fallback_obj = fallback_obj if isinstance(fallback_obj, dict) else {}
            checkout = checkout_from(fallback_obj)
            buyer = checkout.get("buyer")
            buyer = buyer if isinstance(buyer, dict) else {}

        auth = buyer.get("auth")
        auth = auth if isinstance(auth, dict) else {}
        refreshed_euat = str(auth.get("accessToken") or "")
        if refreshed_euat:
            self.state.euat_token = refreshed_euat
            self._ensure_euat_cookie()
        user_id = str(buyer.get("userId") or "")
        if user_id:
            self.state.user_id = user_id
        funding = self._funding_summary(checkout)
        return {
            "buyer_ready": bool(user_id and self.state.euat_token),
            "user_id": user_id,
            "auth_refreshed": bool(refreshed_euat),
            "funding_selected": funding["selected"],
            "funding_available": funding["available"],
            "funding_available_count": funding["available_count"],
            "funding_errors": funding_error_messages,
            "funding_checkpoints": funding_checkpoints,
            "fatal_contingency": fatal_contingency,
        }

    def _protocol_identity_elevation(self) -> dict:
        token = self.state.ec_token
        if not token or not self._is_ec_token(token):
            raise RuntimeError("IDENTITY_ELEVATION_EC_MISSING")
        if not self.state.euat_token:
            raise RuntimeError("IDENTITY_ELEVATION_EUAT_MISSING")
        if not getattr(self.state, "signup_context_ready", False):
            raise RuntimeError("IDENTITY_ELEVATION_SIGNUP_CONTEXT_NOT_READY")

        logger.info("--- Identity elevation: guest checkout -> member checkout ---")
        self._ensure_euat_cookie()

        # Re-enter checkoutweb as an authenticated member on the exact EC token.
        signup_url = self.state.signup_url or self._build_signup_url()
        saved_signup_euat = str(self.state.euat_token or "")

        # AE can invalidate the freshly-issued signup token as soon as the
        # signup/Hermes HTML navigation begins. Hydrate the EC buyer first,
        # while the access token is still fresh, then enter the review route.
        if str(getattr(self.address, "country", "") or "").upper() in {"AE", "BA"}:
            logger.info("AE/BA pre-navigation member hydration started")
            immediate_context = self._query_elevated_context(token, signup_url)
            logger.info(
                "AE/BA pre-navigation hydration: buyer={} errors={} fatal={}",
                immediate_context["buyer_ready"],
                immediate_context["funding_errors"],
                immediate_context["fatal_contingency"] or "none",
            )
            if immediate_context["fatal_contingency"]:
                raise RuntimeError(
                    "IDENTITY_ELEVATION_PAYER_RESTRICTED: "
                    + immediate_context["fatal_contingency"]
                )
            if immediate_context["buyer_ready"]:
                self._ae_pre_hydrated = True
                self._last_elevation_context = immediate_context
                return immediate_context
            # A rejected probe may clear the EUAT cookie; restore it before the
            # existing review-based hydration attempt.
            if saved_signup_euat:
                self.state.euat_token = saved_signup_euat
                self._ensure_euat_cookie()

        self.session.get(
            signup_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": signup_url,
                "Upgrade-Insecure-Requests": "1",
            },
        )
        if (
            str(getattr(self.address, "country", "") or "").upper() in {"AE", "BA"}
            and saved_signup_euat
            and not self.state.euat_token
        ):
            logger.info("AE/BA signup navigation cleared EUAT; restoring signup access token")
            self.state.euat_token = saved_signup_euat
            self._ensure_euat_cookie()

        review_referer = self._hermes_url(billing_lite=True)
        self._load_hagrid_review_context(
            self._hermes_url(),
            self._hermes_url(add_fi_contingency=True),
            review_referer,
        )
        context = self._query_elevated_context(token, review_referer)
        logger.info(
            "Identity elevation context: buyer={} funding_selected={} funding_available={} count={} errors={} fatal={}",
            context["buyer_ready"],
            context["funding_selected"],
            context["funding_available"],
            context["funding_available_count"],
            context["funding_errors"],
            context["fatal_contingency"] or "none",
        )
        if context["fatal_contingency"]:
            raise RuntimeError(
                "IDENTITY_ELEVATION_PAYER_RESTRICTED: " + context["fatal_contingency"]
            )
        if not context["buyer_ready"]:
            raise RuntimeError(
                "IDENTITY_ELEVATION_FAILED: member identity was not attached to the EC checkout"
            )
        self._last_elevation_context = context
        return context

    def _phase4_authorize(self) -> dict:
        try:
            context = self._protocol_identity_elevation()
        except RuntimeError as exc:
            reason = str(exc)
            country = str(getattr(self.address, "country", "") or "").upper()
            ec_token = str(getattr(self.state, "ec_token", "") or "")

            # AE sometimes creates a valid anonymous EC checkout but refuses to
            # attach the newly-created member identity. In that specific case,
            # preserve the current EC/session and try authorize as ANONYMOUS.
            # Hard payer restrictions remain terminal and are not downgraded.
            if (
                country != "AE"
                or "IDENTITY_ELEVATION_PAYER_RESTRICTED" in reason
                or not self._is_ec_token(ec_token)
            ):
                raise

            logger.warning(
                "AE identity elevation unavailable; using current ANONYMOUS EC session once: {}",
                reason,
            )
            strict_before = getattr(self, "_strict_buyer_context", True)
            self._strict_buyer_context = False
            try:
                result = super()._phase4_authorize()
            finally:
                self._strict_buyer_context = strict_before

            if isinstance(result, dict):
                result.setdefault("buyer_mode", "ae_anonymous_fallback")
                result.setdefault(
                    "identity_elevation",
                    {
                        "buyer_ready": False,
                        "anonymous_fallback": True,
                        "reason": reason,
                    },
                )
            return result

        result = super()._phase4_authorize()
        if isinstance(result, dict):
            result.setdefault("buyer_mode", "identity_elevation")
            result.setdefault("identity_elevation", context)
        return result

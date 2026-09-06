"""Tests for the vendor-aware anti-bot challenge classifier."""

from __future__ import annotations

from comic_dl.antibot import (
    BlockVerdict,
    classify_block,
    looks_like_challenge,
)


class TestClassifyBlock:
    """Tests for classify_block()."""

    def test_clean_200_returns_none_vendor(self):
        verdict = classify_block(200, {}, body="<html>normal</html>")
        assert verdict.vendor == "none"
        assert verdict.kind == "none"
        assert verdict.recommended_action is None

    def test_cloudflare_403_with_server_header(self):
        headers = {"server": "cloudflare", "cf-ray": "abc123"}
        body = "<html>challenge-error-title</html>"
        verdict = classify_block(403, headers, body=body)
        assert verdict.vendor == "cloudflare"
        assert verdict.kind == "interstitial"
        assert verdict.recommended_action == "retry"

    def test_cloudflare_503_with_body_markers(self):
        headers = {"server": "nginx"}
        body = '<div id="cf-turnstile"></div>'
        verdict = classify_block(503, headers, body=body)
        assert verdict.vendor == "cloudflare"
        assert verdict.kind == "interstitial"

    def test_cloudflare_cf_mitigated_header(self):
        headers = {"cf-mitigated": "challenge"}
        verdict = classify_block(403, headers)
        assert verdict.vendor == "cloudflare"
        assert verdict.kind == "interstitial"

    def test_cloudflare_geo_block(self):
        headers = {"server": "cloudflare"}
        body = "Error 1009"
        verdict = classify_block(403, headers, body=body)
        assert verdict.vendor == "cloudflare"
        assert verdict.kind == "geo"
        assert verdict.recommended_action == "impersonation"

    def test_cloudflare_rate_limit(self):
        headers = {"server": "cloudflare"}
        body = "Error 1015"
        verdict = classify_block(403, headers, body=body)
        assert verdict.vendor == "cloudflare"
        assert verdict.kind == "rate"
        assert verdict.recommended_action == "rate-limit"

    def test_cloudflare_waf_block(self):
        headers = {"server": "cloudflare"}
        body = "Error 1020"
        verdict = classify_block(403, headers, body=body)
        assert verdict.vendor == "cloudflare"
        assert verdict.kind == "waf"
        assert verdict.recommended_action == "retry"

    def test_recaptcha_v2_detected(self):
        body = '<div class="g-recaptcha" data-sitekey="xxx"></div>'
        verdict = classify_block(200, {}, body=body)
        assert verdict.vendor == "recaptcha"
        assert verdict.kind == "puzzle"
        assert verdict.recommended_action == "webview"

    def test_recaptcha_v3_detected(self):
        body = '<script src="recaptcha/api.js?render=xxx"></script>'
        verdict = classify_block(200, {}, body=body)
        assert verdict.vendor == "recaptcha"
        assert verdict.kind == "score"
        assert verdict.recommended_action == "impersonation"

    def test_hcaptcha_detected(self):
        body = '<script src="hcaptcha.com/1/api.js"></script>'
        verdict = classify_block(200, {}, body=body)
        assert verdict.vendor == "hcaptcha"
        assert verdict.kind == "puzzle"
        assert verdict.recommended_action == "webview"

    def test_turnstile_detected(self):
        body = '<div class="cf-turnstile"></div>'
        verdict = classify_block(200, {}, body=body)
        assert verdict.vendor == "turnstile"
        assert verdict.kind in ("puzzle", "non-interactive")

    def test_datadome_detected_via_cookie(self):
        headers = {"set-cookie": "datadome=xxx"}
        verdict = classify_block(403, headers)
        assert verdict.vendor == "datadome"
        assert verdict.kind == "waf"
        assert verdict.recommended_action == "retry"

    def test_akamai_detected_via_cookie(self):
        headers = {"set-cookie": "_abck=xxx"}
        verdict = classify_block(403, headers)
        assert verdict.vendor == "akamai"
        assert verdict.kind == "waf"

    def test_perimeterx_detected(self):
        headers = {"set-cookie": "_px3=xxx"}
        verdict = classify_block(403, headers)
        assert verdict.vendor == "perimeterx"
        assert verdict.kind == "waf"

    def test_kasada_detected(self):
        headers = {"x-kpsdk-version": "1.0"}
        verdict = classify_block(403, headers)
        assert verdict.vendor == "kasada"
        assert verdict.kind == "waf"

    def test_imperva_detected(self):
        headers = {"set-cookie": "incap_ses_xxx=123"}
        verdict = classify_block(403, headers)
        assert verdict.vendor == "imperva"
        assert verdict.kind == "waf"

    def test_rate_limit_429(self):
        verdict = classify_block(429, {})
        assert verdict.vendor == "unknown"
        assert verdict.kind == "rate"
        assert verdict.recommended_action == "rate-limit"

    def test_server_error_500(self):
        verdict = classify_block(500, {})
        assert verdict.vendor == "unknown"
        assert verdict.kind == "waf"
        assert verdict.recommended_action == "retry"

    def test_honeypot_large_200(self):
        body = "x" * 250_000  # Suspiciously large filler page
        verdict = classify_block(200, {}, body=body)
        assert verdict.honeypot is True
        assert verdict.recommended_action == "webview"

    def test_no_challenge_on_normal_200(self):
        body = "<html><body>Normal content</body></html>"
        verdict = classify_block(200, {}, body=body)
        assert verdict.vendor == "none"
        assert verdict.honeypot is False


class TestCaptchaTieBreaks:
    """Pin vendor tie-breaks when marker sets overlap (P1.3).

    ``data-sitekey`` is shared by every CAPTCHA widget, so it must never be
    read as reCAPTCHA v2; v3 must win over v2; Turnstile markers must win
    over reCAPTCHA (both embed on Cloudflare hosts).
    """

    def test_turnstile_with_sitekey_is_not_recaptcha_v2(self):
        body = (
            '<div class="cf-turnstile" data-sitekey="0xAAAA"></div>'
            '<form><input type="email" data-callback="onTurnstile"></form>'
        )
        verdict = classify_block(200, {}, body=body)
        assert verdict.vendor == "turnstile"
        assert verdict.kind == "puzzle"

    def test_turnstile_site_keeping_recaptcha_marker_is_still_recaptcha(self):
        # A page with an explicit g-recaptcha marker but no turnstile markers
        # is reCAPTCHA; sitekey alone must not have flipped it.
        body = (
            '<div class="g-recaptcha" data-sitekey="6Le-xxx"></div>'
            '<script src="recaptcha/api.js"></script>'
        )
        verdict = classify_block(200, {}, body=body)
        assert verdict.vendor == "recaptcha"
        assert verdict.kind == "puzzle"

    def test_turnstile_non_interactive_without_widget(self):
        body = '<script src="challenges.cloudflare.com/turnstile"></script>'
        verdict = classify_block(200, {}, body=body)
        assert verdict.vendor == "turnstile"
        assert verdict.kind == "non-interactive"

    def test_hcaptcha_sitekey_is_not_recaptcha_v2(self):
        body = (
            '<div class="h-captcha" data-sitekey="10000000"></div>'
            '<script src="hcaptcha.com/1/api.js"></script>'
        )
        verdict = classify_block(200, {}, body=body)
        assert verdict.vendor == "hcaptcha"
        assert verdict.kind == "puzzle"

    def test_recaptcha_v3_wins_over_v2_when_both_present(self):
        body = (
            '<div class="g-recaptcha" data-sitekey="6Le-xxx"></div>'
            '<script src="recaptcha/api.js?render=6Le-xxx"></script>'
        )
        verdict = classify_block(200, {}, body=body)
        assert verdict.vendor == "recaptcha"
        assert verdict.kind == "score"

    def test_bare_sitekey_is_not_any_captcha(self):
        body = '<div data-sitekey="abcd"></div>'
        verdict = classify_block(200, {}, body=body)
        assert verdict.vendor == "none"
        assert verdict.kind == "none"


class TestHoneypotBoundaries:
    """Pin the honeypot size thresholds (P1.4)."""

    def test_under_50k_is_never_honeypot(self):
        verdict = classify_block(200, {}, body="x" * 49_999)
        assert verdict.honeypot is False

    def test_at_50k_threshold_is_not_honeypot_without_selectors(self):
        verdict = classify_block(200, {}, body="x" * 50_000)
        assert verdict.honeypot is False

    def test_over_200k_no_selectors_is_honeypot(self):
        verdict = classify_block(200, {}, body="x" * 200_001)
        assert verdict.honeypot is True

    def test_over_50k_with_selectors_all_missing_is_honeypot(self):
        verdict = classify_block(
            200,
            {},
            body="x" * 60_000,
            expected_selectors=["chapter-content", "page-list"],
        )
        assert verdict.honeypot is True

    def test_over_50k_with_expected_selector_present_is_not_honeypot(self):
        verdict = classify_block(
            200,
            {},
            body="x" * 60_000 + "chapter-content",
            expected_selectors=["chapter-content", "page-list"],
        )
        assert verdict.honeypot is False


class TestLooksLikeChallenge:
    """Tests for backwards-compatible looks_like_challenge()."""

    def test_returns_false_on_200(self):
        assert looks_like_challenge(200, {}) is False

    def test_returns_false_on_unknown_403(self):
        assert looks_like_challenge(403, {"server": "nginx"}) is False

    def test_returns_true_on_cf_403(self):
        headers = {"server": "cloudflare", "cf-ray": "abc"}
        assert looks_like_challenge(403, headers) is True

    def test_returns_true_on_cf_503(self):
        headers = {"server": "cloudflare"}
        assert looks_like_challenge(503, headers) is True

    def test_returns_true_on_cf_body_markers(self):
        headers = {"server": "nginx"}
        body = '<div id="cf-turnstile"></div>'
        assert looks_like_challenge(403, headers, body) is True

    def test_returns_false_on_non_cf_403(self):
        headers = {"server": "nginx"}
        assert looks_like_challenge(403, headers) is False


class TestBlockVerdict:
    """Tests for BlockVerdict dataclass."""

    def test_default_recommended_action_is_none(self):
        verdict = BlockVerdict(
            vendor="none",
            kind="none",
            reason=None,
            challenge=None,
            honeypot=False,
        )
        assert verdict.recommended_action is None

    def test_recommended_action_can_be_set(self):
        verdict = BlockVerdict(
            vendor="cloudflare",
            kind="interstitial",
            reason="test",
            challenge=None,
            honeypot=False,
            recommended_action="webview",
        )
        assert verdict.recommended_action == "webview"

from enum import StrEnum
from playwright.sync_api import TimeoutError as PlaywrightTimeout

class FailureKind(StrEnum):
    NETWORK_BLOCKED = 'network_blocked'
    PAGE_TIMEOUT = 'page_timeout'
    LOGIN_EXPIRED = 'login_expired'
    CAPTCHA = 'captcha'
    FORM_ERROR = 'form_error'

class BrowserProblem(Exception):
    def __init__(self, kind, reason):
        super().__init__(reason)
        self.kind = FailureKind(kind)

def classify_exception(exc):
    if isinstance(exc, BrowserProblem): return exc.kind
    message = str(exc).lower()
    if any(s in message for s in ['err_tunnel_connection_failed', 'proxyerror', 'domain forbidden', '403 forbidden', 'err_proxy_connection_failed']):
        return FailureKind.NETWORK_BLOCKED
    if isinstance(exc, (PlaywrightTimeout, TimeoutError)):
        return FailureKind.PAGE_TIMEOUT
    return FailureKind.FORM_ERROR

def challenge_kind(page):
    """Detect a challenge; never solve one. Read only presence, never its token value."""
    text = page.locator('body').inner_text(timeout=3000).lower()
    if page.locator('input[type=password]:visible').count(): return FailureKind.LOGIN_EXPIRED
    if any(w in text for w in ['短信验证', '短信验证码', 'verification code', 'one-time password', 'session expired', '登录已失效']):
        return FailureKind.LOGIN_EXPIRED
    captcha = any(w in text for w in ['加载验证码', '验证码', 'captcha', '人机验证']) or page.locator('iframe[src*="recaptcha"]:visible,iframe[src*="hcaptcha"]:visible').count()
    if captcha:
        completed = page.locator('textarea[name="g-recaptcha-response"],textarea[name="h-captcha-response"]').evaluate_all('els => els.some(e => e.value.length > 0)')
        if not completed: return FailureKind.CAPTCHA
    return None

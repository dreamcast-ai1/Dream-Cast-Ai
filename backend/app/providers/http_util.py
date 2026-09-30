import httpx

from .base import ErrorCode, ProviderError


def http_client(timeout: float) -> httpx.Client:
    """Factory kept separate so tests can inject a mock transport (no real API calls in tests)."""
    return httpx.Client(timeout=timeout)


def raise_for_provider_status(res: httpx.Response, what: str, *, no_quota_statuses: tuple[int, ...] = (402,)) -> None:
    """Maps HTTP failures to DreamCast error codes with user-safe messages. Provider bodies are never shown to users."""
    code = res.status_code
    if code < 400:
        return
    if code in (401, 403):
        raise ProviderError(ErrorCode.AUTHENTICATION_ERROR, f"HTTP {code}",
                            message=f"The {what} provider rejected its API key. An administrator needs to check the configuration.")
    if code in no_quota_statuses:
        raise ProviderError(ErrorCode.QUOTA_EXCEEDED, f"HTTP {code}", message=f"The {what} provider's usage quota has been reached. Please try again later.")
    if code == 429:
        raise ProviderError(ErrorCode.RATE_LIMITED, "HTTP 429", transient=True,
                            message=f"The {what} provider is rate-limiting requests. Please try again shortly.")
    if code >= 500 or code == 503:
        raise ProviderError(ErrorCode.PROVIDER_UNAVAILABLE, f"HTTP {code}", transient=True,
                            message=f"{what.capitalize()} generation failed because the configured {what} provider is unavailable.")
    raise ProviderError(ErrorCode.INVALID_REQUEST, f"HTTP {code}: {res.text[:300]}",
                        message=f"The {what} provider couldn't accept this request. Try changing the prompt or options.")


def network_error(e: Exception, what: str) -> ProviderError:
    kind = "timed out" if isinstance(e, httpx.TimeoutException) else "could not be reached"
    return ProviderError(ErrorCode.PROVIDER_UNAVAILABLE, f"{type(e).__name__}: {e}", transient=True,
                         message=f"{what.capitalize()} generation failed because the configured {what} provider {kind}.")

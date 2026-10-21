# Phase 1: HTTP client hardening

Scope: `custom_components/opus_greennet/api.py` only. No entity, platform or
config-flow behaviour changed.

## Context

The OPUS gateway offers its REST API over plain HTTP with Basic authentication.
Transport encryption cannot be added on the client side. This phase limits what
a hostile or misconfigured peer can do to the integration and keeps the
credentials from leaving the configured gateway.

## Changes

| Change | Reason | OWASP API Top 10 |
|---|---|---|
| `allow_redirects=False` for requests and stream | The Authorization header must never follow a redirect to another host. A 3xx now fails like any non-2xx status. | API7 SSRF, API2 Broken Authentication |
| `_read_bounded_text` with `MAX_RESPONSE_SIZE` (8 MiB), checking `Content-Length` and the received bytes | Previously only the stream was bounded. Ordinary responses are now bounded too. | API4 Unrestricted Resource Consumption |
| `validate_host` and `validate_port` in `OpusGatewayClient.__init__` | Only an IP literal or hostname is accepted. URLs, paths, credentials, ports, queries and whitespace are rejected, so the configured value cannot alter the request target. | API7 SSRF, API8 Security Misconfiguration |
| New `OpusInvalidHostError(OpusConnectionError)` | Existing handlers for connection errors keep working. | n/a |

## Unchanged by design

- Requests are never retried.
- HTTP status and document status are still checked separately.
- The HTTP-only transport remains a documented residual risk; the gateway
  belongs in a trusted, restricted network segment.

## Tests

`tests/test_api_phase1_hardening.py` covers host and port validation as pure
functions. The change was prepared without running the suite; run the CI
(`ruff`, `mypy`, `pytest`, coverage gate) before merging.

## Follow-up checks for the reviewer

- Confirm that no existing test builds the client with a host that is now
  rejected (for example one containing an underscore).
- Confirm that the fake gateway sends `Content-Length` or chunked bodies below
  the limit.
- Add a test for a redirect response and an oversized body in the fake gateway
  (planned for the next iteration of this phase).

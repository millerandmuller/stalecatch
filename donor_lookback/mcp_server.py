"""F1 Memory server: MCP (Streamable HTTP) over the SQLite store. Parents are enforced by Store.derive, not by a prompt."""
import argparse
import hmac
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .fetcher import donor_url_problem
from .intake import IntakeError, intake_url
from .services import Services, build_services
from .store import NotFoundError, ParentError

INSTRUCTIONS = (
    "Memory with a donor record for every fact. Remember a deadline with remember_from_url (it stores the source, fetch "
    "time and a verbatim excerpt). Every item you build from it must be saved with derive and its parent ids. "
    "Use lookback to see everything built on a source."
)


def create_mcp(svc: Services, host: str = "127.0.0.1", port: int = 8765) -> FastMCP:
    allowed = [h for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h]
    security = TransportSecuritySettings(enable_dns_rebinding_protection=bool(allowed) or host in ("127.0.0.1", "localhost"),
                                         allowed_hosts=allowed or [f"{host}:{port}", "localhost:*", "127.0.0.1:*"],
                                         allowed_origins=["*"] if allowed else ["http://localhost:*", "http://127.0.0.1:*"])
    mcp = FastMCP("donor-lookback", instructions=INSTRUCTIONS, host=host, port=port, transport_security=security)
    store = svc.store

    @mcp.tool()
    def remember_from_url(url: str, label: str = "") -> dict:
        """Fetch a web page, extract the deadline claim with a verbatim excerpt, and store it as a donor."""
        problem = donor_url_problem(url, svc.settings.public_base_url)
        if problem:
            raise ValueError(problem)
        try:
            return intake_url(store, svc.gateway, svc.fetcher, svc.settings, url, label=label or None)
        except IntakeError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    def add_donor(url: str, claim: str, excerpt: str, label: str = "") -> dict:
        """Store a donor you already verified: source URL, a one-sentence claim and the verbatim excerpt."""
        problem = donor_url_problem(url, svc.settings.public_base_url)
        if problem:
            raise ValueError(problem)
        return store.add_donor(url=url, claim=claim, excerpt=excerpt, label=label or None, fetch_via="mcp-client")

    @mcp.tool()
    def derive(parents: list[str], kind: str, content: str, title: str = "", sent: bool = False, recipient: str = "") -> dict:
        """Save something you built (calendar entry, plan, message). parents = ids of the donors/products it rests on. Errors if none."""
        try:
            return store.derive(parents=parents, kind=kind, content=content, title=title or None,
                                sent=sent, recipient=recipient or None)
        except ParentError as exc:
            raise ValueError(str(exc)) from exc

    @mcp.tool()
    def mark_sent(product_id: str, recipient: str) -> dict:
        """Record that a product was sent to a recipient (outbox only; nothing is actually sent)."""
        try:
            return store.mark_sent(product_id, recipient)
        except NotFoundError as exc:
            raise ValueError(f"unknown product {product_id}") from exc

    @mcp.tool()
    def lookback(donor_id: str) -> dict:
        """Everything built on this donor, sent items first."""
        try:
            return store.lookback(donor_id)
        except NotFoundError as exc:
            raise ValueError(f"unknown donor {donor_id}") from exc

    @mcp.tool()
    def list_memory() -> dict:
        """All donors and products with their status."""
        return {"donors": store.list_donors(), "products": store.list_products()}

    return mcp


class BearerAuth:
    """ASGI middleware: every HTTP request needs 'Authorization: Bearer <token>' (constant-time compare)."""

    def __init__(self, app, token: str):
        self.app, self._token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"")
            scheme, _, supplied = auth.partition(b" ")
            if scheme.lower() != b"bearer" or not hmac.compare_digest(supplied.strip(), self._token):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b'{"error":"missing or invalid bearer token"}'})
                return
        await self.app(scope, receive, send)


def main() -> None:
    ap = argparse.ArgumentParser(description="StaleCatch memory MCP server (Streamable HTTP)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--tls-cert", help="PEM certificate: serve HTTPS (needed by managed MCP clients such as NemoClaw)")
    ap.add_argument("--tls-key", help="PEM private key for --tls-cert")
    args = ap.parse_args()
    mcp = create_mcp(build_services(), args.host, args.port)
    token = os.environ.get("MCP_BEARER_TOKEN", "")
    if args.host not in ("127.0.0.1", "localhost") and not token:
        raise SystemExit("Refusing to listen beyond loopback without MCP_BEARER_TOKEN set.")
    if not token:
        mcp.run(transport="streamable-http")
        return
    import uvicorn
    app = BearerAuth(mcp.streamable_http_app(), token)
    uvicorn.run(app, host=args.host, port=args.port, ssl_certfile=args.tls_cert, ssl_keyfile=args.tls_key)


if __name__ == "__main__":
    main()

# Toolbox: concierge-tools (MCP)

`azure.yaml` wires `concierge-tools` as an `azure.ai.toolbox` service with
one MCP tool entry, targeting `${ORDER_LOOKUP_MCP_ENDPOINT}` with
`authType: CustomKeys`. MCP is GA on T1 (`docs/04` §"Code interpreter on
tier 1" — "MCP is also GA, so a two-tool (search + code interpreter) agent
is entirely tier 1"), so this is the intended way to give a T1 agent tools
beyond the built-ins.

This sample does not ship an MCP server — connecting a real order-lookup
backend is out of scope for a hello-world sample, and every real deployment
points this at a different backend anyway. Two ways to fill it in:

1. **Point it at your own MCP server.** Set `ORDER_LOOKUP_MCP_ENDPOINT` to
   its URL and pick the `authType` that matches how it authenticates
   (`CustomKeys`, `OAuth2`, or a managed-identity variant — see
   `docs/05-tier2-hosted-agents.md` §4.2 for the full auth-type table; the
   T2 doc covers it in more depth but the same connection auth types apply
   here).
2. **Stub it for a local smoke test.** Any MCP server exposing
   `lookup_order(order_id: str)`, `list_recent_orders(customer_id: str)`,
   and `check_return_eligibility(order_id: str)` over streamable HTTP
   satisfies `concierge.instructions.md`'s tool contract. The
   `MCPStreamableHTTPTool` client shape referenced in
   `docs/05-tier2-hosted-agents.md` §5.2 is the same one Foundry's toolbox
   connects through server-side — useful as a reference if you're writing
   the stub from scratch.

## The auth-type trap (same one T2 hits)

**`authType` binds to the MCP server's URL, not to the agent.** An
incorrect audience fails authentication even when RBAC is otherwise
correct — see `docs/05-tier2-hosted-agents.md` §4.2 if you swap in a real
backend and start getting silent auth failures.

from mcp.server import MCPServer

from app.domain_tools.risk_case_fetch import risk_case_fetch


server = MCPServer(
    name="risk-investigation",
    title="Risk Investigation MCP Server",
    description="MCP server exposing risk investigation capabilities.",
    version="0.1.0",
)


@server.tool(
    name="risk_case_fetch",
    description="Fetch the canonical investigation context and findings for a risk case.",
)
def fetch_risk_case(case_id: str) -> dict:
    result = risk_case_fetch(case_id)

    return result.model_dump(mode="json")


if __name__ == "__main__":
    server.run("stdio")

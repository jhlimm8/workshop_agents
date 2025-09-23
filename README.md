# workshop_agents
Templates of Agentic Systems in preparation of the Workshop

**Problem Statement**: Agents have been promised to deliver great value in the market, but what they actually are remains unclear. This uncertainty leads to many avoiding having to build their own agents, potentially missing out on the resultant benefits.

**Goal**: Demystify Agents so people are motivated to build them and reap the benefits.

**Proposed Solution**: Provide Templates of Agentic Systems that can be easily understood, modified, and reused to enable people to build Agents to solve their own problems. 

Setup:
As the outlook assistant agent uses another repo's mcp server, the other repo needs to be setup as well.
1. Install dependencies
```
Install git
Install uv

git clone https://github.com/jhlimm8/workshop_agents
cd workshop_agents
uv sync

cd src/outlook_assistant_agent/tools
git clone https://github.com/elyxlz/microsoft-mcp
uv sync
```
2. Define Environment Variables

here MICROSOFT_MCP_CLIENT_ID has to be temporarily set without the prefix 'api://' for calling the mcp outlook authentication tool
```
windows_cmd:
set GOOGLE_API_KEY = ''
set MICROSOFT_MCP_CLIENT_ID='4196953a-89de-43f7-8317-0391857f0124'
set MICROSOFT_MCP_TENANT_ID=consumers

uv run authenticate.py

set MICROSOFT_MCP_CLIENT_ID='MICROSOFT_MCP_CLIENT_ID='api://4196953a-89de-43f7-8317-0391857f0124'
```
To ensure the proper env vars are read for the jupyter notebooks, also create a .env file with the 3 env vars
```
windows_cmd
cd ../../../
touch 
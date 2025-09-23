import os
import asyncio
import json
import ast
import sys
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime, timedelta
import re

from google import genai
from google.genai import types

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

# Configuration
APP_ID = os.getenv("MICROSOFT_MCP_CLIENT_ID")
TENANT = os.getenv("MICROSOFT_MCP_TENANT_ID", "consumers")

# Define which MCP tools you want to expose to Gemini
ALLOWED_TOOLS = {
    "list_events",
    "create_event",
}

def _sanitize_openapi_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively sanitize JSON schema so it is compatible with Gemini function declarations.
    Removes unsupported fields like '$schema', 'additionalProperties', etc.
    Ensures top-level is always an object with properties.
    """
    if not schema:
        return {"type": "object", "properties": {}}

    def prune(s: Any) -> Any:
        if isinstance(s, dict):
            # Drop disallowed keys
            s = {k: prune(v) for k, v in s.items()
                 if k not in ("$schema", "additionalProperties", "default", "examples")}
            
            # Recurse into schema constructs
            if "properties" in s and isinstance(s["properties"], dict):
                s["properties"] = {pk: prune(pv) for pk, pv in s["properties"].items()}
            if "items" in s:
                s["items"] = prune(s["items"])
            if "oneOf" in s:
                s["oneOf"] = [prune(x) for x in s["oneOf"]]
            if "anyOf" in s:
                s["anyOf"] = [prune(x) for x in s["anyOf"]]
            if "allOf" in s:
                s["allOf"] = [prune(x) for x in s["allOf"]]
            return s
        elif isinstance(s, list):
            return [prune(x) for x in s]
        return s

    pruned = prune(schema)

    # Ensure top-level structure is always object
    if pruned.get("type") != "object":
        return {
            "type": "object",
            "properties": pruned.get("properties", {}),
            "required": pruned.get("required", []),
        }
    return pruned

def _extract_text_from_mcp_result(result) -> str:
    """
    Convert an MCP ToolResult into simple text/JSON the model can consume.
    """
    parts = getattr(result, "content", None)
    if isinstance(parts, list):
        texts = []
        for p in parts:
            kind = getattr(p, "type", None) or (isinstance(p, dict) and p.get("type"))
            if kind == "text":
                texts.append(getattr(p, "text", None) or (isinstance(p, dict) and p.get("text")) or "")
            elif kind in ("json", "object", "data"):
                data = getattr(p, "data", None) or (isinstance(p, dict) and p.get("data"))
                texts.append(json.dumps(data, ensure_ascii=False))
        if texts:
            return "\n".join(t for t in texts if t)
    # Fallback: stringify
    try:
        return json.dumps(result, default=lambda o: getattr(o, "__dict__", str(o)), ensure_ascii=False)
    except Exception:
        return str(result)

# ================================
# UI OPTION 1: Simple Text-based Approval
# ================================
def display_simple_approval(function_calls):
    """Simple text-based display of planned actions"""
    print("\n" + "="*60)
    print("🔍 PLANNED ACTIONS - Please Review:")
    print("="*60)
    
    for i, fc in enumerate(function_calls, 1):
        print(f"\n{i}. {fc.name.upper()}")
        print("-" * 40)
        
        args = fc.args or {}
        for key, value in args.items():
            if key == "account_id":
                continue  # Skip showing account_id as it's internal
            print(f"   {key}: {value}")
    
    print("\n" + "="*60)
    response = input("Proceed with these actions? (y/n/details): ").strip().lower()
    
    if response == 'details':
        for i, fc in enumerate(function_calls, 1):
            print(f"\nDetailed view for action {i} ({fc.name}):")
            print(json.dumps(fc.args, indent=2))
        return input("\nProceed? (y/n): ").strip().lower() == 'y'
    
    return response == 'y'

# ================================
# Main UI Selection Function
# ================================
async def get_user_approval(function_calls):
    """Main function to get user approval with UI choice"""
    
    if not function_calls:
        return []

    approved = display_simple_approval(function_calls)
    return function_calls if approved else []

    
    print("\n🎨 Choose approval interface:")
    print("1. Simple text-based (fastest)")
    
    choice = input("Select option (default: 1): ").strip() or '1'
    
    if choice == '1':
        approved = display_simple_approval(function_calls)
        return function_calls if approved else []
    

# ================================
# Modified main execution function
# ================================
async def setup_mcp_connection(app_id: str, tenant: str):
    """Set up MCP connection and get account information"""
    mcp_path = Path("tools/microsoft-mcp").resolve()
    
    print(f"Using MCP path: {mcp_path}")
    print(f"APP_ID: {app_id[:8] + '...' if app_id else 'None'}")
    print(f"TENANT: {tenant}")
    
    transport = StdioTransport(
        command="uv",
        args=["run", "microsoft-mcp"],
        env={
            "MICROSOFT_MCP_CLIENT_ID": app_id,
            "MICROSOFT_MCP_TENANT_ID": tenant,
        },
        cwd=str(mcp_path)
    )
    
    client = Client(transport)
    return client

async def get_account_id(mcp_client):
    """Get the first account ID for use in tool calls"""
    try:
        accounts_result = await mcp_client.call_tool("list_accounts", {})
        print(f"Fetched accounts successfully: {accounts_result}.")
        print(f"Account content: {accounts_result.content}")
        if accounts_result.content:
            accounts_text = accounts_result.content[0].text
            accounts_list = ast.literal_eval(accounts_text)
            if accounts_list and isinstance(accounts_list, list):
                return accounts_list[0]['account_id']
            elif accounts_list and isinstance(accounts_list, dict):
                return accounts_list.get('account_id')
    except Exception as e:
        print(f"Error getting account ID: {e}")
    return None

async def call_llm_with_tools(prompt: str, app_id: str, tenant: str):
    """Main function that connects MCP tools to Gemini LLM with approval step"""
    
    # Connect to MCP server using our working connection code
    mcp_client = await setup_mcp_connection(app_id, tenant)
    
    async with mcp_client:
        print("Connected to MCP server successfully!")
        
        # Give server time to initialize
        await asyncio.sleep(0.5)
        
        # Get account ID
        account_id = await get_account_id(mcp_client)
        if not account_id:
            print("Failed to get account ID")
            return "Sorry, I couldn't access your Microsoft account."
        
        print(f"Using account ID: {account_id}")
        
        # Discover server tools and filter to allowed ones
        tools_resp = await mcp_client.list_tools()
        selected = [t for t in tools_resp if t.name in ALLOWED_TOOLS]
        
        print(f"Available tools: {[t.name for t in selected]}")
        
        # Convert MCP tools to Gemini function declarations
        function_decls = []
        for t in selected:
            params_schema = _sanitize_openapi_schema(getattr(t, "inputSchema", {}) or {})
            function_decls.append({
                "name": t.name,
                "description": getattr(t, "description", "") or f"MCP tool '{t.name}'.",
                "parameters": params_schema,
            })
        
        # Set up Gemini with tools
        tool = types.Tool(function_declarations=function_decls)
        tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode="ANY",
                allowed_function_names=[fd["name"] for fd in function_decls],
            )
        )
        
        # System instruction with account info
        system_instruction = f"""You are a helpful assistant that helps users manage their Outlook calendars. 
        You have access to Microsoft Graph API tools through MCP.
        
        IMPORTANT: The account ID is '{account_id}' and must be included as the first parameter in ALL tool calls.
        Today's date is {datetime.now().strftime('%Y-%m-%d')} UTC, but note that I live in an UTC+8 timezone, so offset 8 hours from any date you provide.
        
        When creating events, use proper ISO 8601 datetime format (e.g., '2024-01-15T10:00:00Z').
        Always be helpful and provide clear confirmations of actions taken."""
        
        client = genai.Client()
        contents = [prompt]
    
        # Start single tool calling process (with approval)
        
        resp = client.models.generate_content(
            model="gemini-2.0-flash-exp",
            contents=contents,
            config=types.GenerateContentConfig(
                tools=[tool],
                tool_config=tool_config,
                temperature=0,
                system_instruction=system_instruction
            ),
        )
        
        # Check for function calls
        function_calls = []
        for cand in resp.candidates or []:
            for part in cand.content.parts or []:
                fc = getattr(part, "function_call", None)
                if fc:
                    function_calls.append(fc)

        
        # *** NEW: Get user approval before executing ***
        print(f"🤖 AI wants to execute {len(function_calls)} action(s)...")
        approved_calls = await get_user_approval(function_calls)
        
        if not approved_calls:
            return "❌ Action cancelled by user. No changes were made to your calendar."
        
        if len(approved_calls) < len(function_calls):
            print(f"📝 Executing {len(approved_calls)} out of {len(function_calls)} actions...")
        
        # Add the model's function calls to conversation (only approved ones)
        model_parts = []
        for fc in approved_calls:
            model_parts.append(types.Part.from_function_call(name=fc.name, args=fc.args or {}))
        contents.append(types.ModelContent(parts=model_parts))
        
        # Execute approved function calls
        user_parts = []
        for fc in approved_calls:
            print(f"⚡ Executing {fc.name}...")
            
            tool_args = fc.args or {}
            if fc.name != "list_accounts" and "account_id" not in tool_args:
                tool_args = {"account_id": account_id, **tool_args}
            
            try:
                mcp_result = await mcp_client.call_tool(fc.name, tool_args)
                result_text = _extract_text_from_mcp_result(mcp_result)
                
                print(f"✅ {fc.name} completed")
                return result_text
                user_parts.append(types.Part.from_function_response(
                    name=fc.name,
                    response={"result": result_text},
                ))
                
            except Exception as e:
                print(f"❌ Error executing {fc.name}: {e}")
                user_parts.append(types.Part.from_function_response(
                    name=fc.name,
                    response={"error": str(e)},
                ))
            

async def main():
    if not APP_ID:
        print("Error: MICROSOFT_MCP_CLIENT_ID environment variable not set!")
        return
    
    # Test prompts
    test_prompts = [
        "What events do I have this week?",
        "Block out my calendar for the entirety of tomorrow. I want to work on building agents.",
        "Do I have any meetings today?",
    ]
    
    print("=== Outlook Assistant with MCP + Gemini (With Approval) ===\n")
    
    prompt = input("Enter your request (or press Enter for default): ").strip()
    if not prompt:
        prompt = test_prompts[1]
    
    print(f"\nUser: {prompt}")
    
    try:
        response = await call_llm_with_tools(prompt, APP_ID, TENANT)
        if response:
            print(f"\nAssistant: {response}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
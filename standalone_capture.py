import asyncio
import json
import os
import re
import sys

# Ensure 'mcp' SDK is installed or instruct the user
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:
    print("[!] Python 'mcp' SDK is not installed. Please run: pip install mcp", file=sys.stderr)
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HEADERS_FILE = os.path.join(SCRIPT_DIR, 'headers.json')

server_params = StdioServerParameters(
    command="npx",
    args=["-y", "chrome-devtools-mcp@latest", "--browserUrl", "http://127.0.0.1:9222"]
)

async def capture_headers(slug):
    print(f"[+] Connecting to Google Chrome via Chrome DevTools MCP server...")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            print("[+] Initializing session...")
            await session.initialize()
            
            # Newer chrome-devtools-mcp requires an explicit pageId on page tools
            pages = await session.call_tool("list_pages", {})
            pages_text = "".join(i.text for i in pages.content if hasattr(i, 'text'))
            page_id = None
            for line in pages_text.splitlines():
                m = re.match(r"\s*(\d+):.*quora\.com", line)
                if m:
                    page_id = int(m.group(1))
                    break
            if page_id is None:
                m = re.search(r"^\s*(\d+):", pages_text, re.M)
                page_id = int(m.group(1)) if m else 1
            print(f"[+] Using page {page_id}")

            url = f"https://www.quora.com/profile/{slug}/answers"
            print(f"[+] Navigating the active tab to: {url}")
            try:
                await session.call_tool("navigate_page", {
                    "pageId": page_id,
                    "type": "url",
                    "url": url
                })
                # Give it a few seconds to load the page
                await asyncio.sleep(4.0)
                
                print("[+] Scrolling down the page to trigger GraphQL requests...")
                await session.call_tool("evaluate_script", {
                    "pageId": page_id,
                    "function": "() => { window.scrollTo(0, document.documentElement.scrollHeight); return 'scrolled'; }"
                })
                # Give it a few seconds to fire and finish the network request
                await asyncio.sleep(3.0)
            except Exception as err:
                print(f"[!] Warning: Navigation or scrolling failed: {err}. Checking logs anyway...")
            
            print("[+] Querying network requests captured by Chrome...")
            # We filter for 'fetch' resources to find Quora GraphQL calls
            result = await session.call_tool("list_network_requests", {"pageId": page_id, "resourceTypes": ["fetch"]})

            
            # The tool result content is usually a list of text objects
            content_text = ""
            for item in result.content:
                if hasattr(item, 'text'):
                    content_text += item.text
                elif isinstance(item, dict) and 'text' in item:
                    content_text += item['text']
            
            # Find a GraphQL request matching answers or questions query
            req_id = None
            for line in content_text.splitlines():
                if "UserProfileAnswersMostRecent_RecentAnswers_Query" in line or "UserProfileQuestionsList_Questions_Query" in line:
                    # Line looks like: "reqid=63 POST https://www.quora.com/..."
                    parts = line.split(" ")
                    for p in parts:
                        if p.startswith("reqid="):
                            req_id = int(p.split("=")[1])
                            break
                    if req_id is not None:
                        break
            
            if req_id is None:
                print("[!] Could not find active 'UserProfileAnswersMostRecent_RecentAnswers_Query' or 'UserProfileQuestionsList_Questions_Query' request.")
                print("    Please reload the Quora profile page in Chrome to generate network logs, then run this script again.")
                return
                
            print(f"[+] Found matching request ID: {req_id}. Retrieving details...")
            details = await session.call_tool("get_network_request", {"pageId": page_id, "reqid": req_id})
            
            details_text = ""
            for item in details.content:
                if hasattr(item, 'text'):
                    details_text += item.text
                elif isinstance(item, dict) and 'text' in item:
                    details_text += item['text']
            
            # Parse headers from get_network_request output
            # Output is formatted like:
            # - header-name:value
            captured_headers = {}
            in_req_headers = False
            for line in details_text.splitlines():
                if line.startswith("### Request Headers"):
                    in_req_headers = True
                    continue
                if line.startswith("### Request Body") or line.startswith("### Response"):
                    in_req_headers = False
                    continue
                if in_req_headers and line.startswith("- "):
                    # Format: "- name:value"
                    raw_header = line[2:].strip()
                    if raw_header.startswith(":"):
                        continue
                    if ":" in raw_header:
                        k, v = raw_header.split(":", 1)
                        kl = k.lower()
                        if kl in ("content-length", "accept-encoding"):
                            continue
                        captured_headers[kl] = v.strip()
            
            # Parse request body to extract UID
            req_body_str = None
            in_req_body = False
            for line in details_text.splitlines():
                if line.startswith("### Request Body"):
                    in_req_body = True
                    continue
                if in_req_body:
                    req_body_str = line.strip()
                    break
            
            uid = None
            if req_body_str:
                try:
                    body_json = json.loads(req_body_str)
                    uid = body_json.get("variables", {}).get("uid")
                    print(f"[+] Captured profile numeric UID from request: {uid}")
                except Exception:
                    pass

            # Verify we captured key headers
            required_keys = ["cookie", "quora-formkey", "quora-turnstile-token"]
            missing = [k for k in required_keys if k not in captured_headers]
            
            if missing:
                print(f"[!] Warning: Missing expected headers: {missing}")
                
            # Write structured data to headers.json
            output_data = {
                "uid": uid,
                "headers": captured_headers
            }
            with open(HEADERS_FILE, 'w') as f:
                json.dump(output_data, f, indent=2)
                
            print(f"[+] Successfully saved headers and UID to {HEADERS_FILE}!")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Capture Quora GraphQL headers via Chrome DevTools MCP.")
    parser.add_argument('--slug', type=str, default='Alan-Kay-11', help="Quora user profile slug (e.g. Alan-Kay-11, Miguel-Paraz)")
    args = parser.parse_args()
    
    try:
        asyncio.run(capture_headers(args.slug))
    except Exception as e:
        print(f"[!] Error: {e}", file=sys.stderr)

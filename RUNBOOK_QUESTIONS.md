# Runbook: Download Quora Questions via GraphQL API

Operational guide for Claude to extract all questions asked by a Quora user.
Run inside an authenticated Chrome browser session using the Claude-in-Chrome extension.

---

## Prerequisites

- Chrome tab open and logged in to Quora
- Claude-in-Chrome MCP extension active
- Tab ID known (get via `mcp__claude-in-chrome__tabs_context_mcp`)

---

## Step 1 — Navigate to the questions page

```
https://www.quora.com/profile/<username>/questions
```

Tool: `mcp__claude-in-chrome__navigate`

Wait for page to load (title should show username).

---

## Step 2 — Unregister the Service Worker

Quora's Service Worker intercepts all fetch calls before page-level JS can see
them. Unregister it so the interceptor injected in Step 3 works.

```javascript
(async () => {
  const regs = await navigator.serviceWorker.getRegistrations();
  for (const r of regs) await r.unregister();
  return 'Unregistered: ' + regs.length;
})()
```

Tool: `mcp__claude-in-chrome__javascript_tool`

Expected: `"Unregistered: 1"`

---

## Step 3 — Inject fetch interceptor to capture session headers

Inject via a `<script>` tag so it runs in the **main world** (not the extension
isolated world). Stores the last 3 captured GraphQL requests in `localStorage`.

```javascript
const s = document.createElement('script');
s.textContent = `
  const _of = window.fetch;
  window.fetch = async function(...args) {
    const url = typeof args[0]==='string' ? args[0] : (args[0]?.url||'');
    if (url.includes('gql_para') || url.includes('graphql')) {
      const opts = args[1]||{};
      let headers = {};
      if (opts.headers instanceof Headers) opts.headers.forEach((v,k) => { headers[k]=v; });
      else if (opts.headers) headers = opts.headers;
      let body = opts.body ? (typeof opts.body==='string' ? opts.body : JSON.stringify(opts.body)) : '';
      const prev = JSON.parse(localStorage.getItem('_gql_capture3')||'[]');
      prev.unshift({ url, headers, body: body.substring(0,500) });
      localStorage.setItem('_gql_capture3', JSON.stringify(prev.slice(0,3)));
    }
    return _of.apply(this, args);
  };
`;
document.head.appendChild(s);
'Interceptor injected'
```

---

## Step 4 — Trigger a real API request to capture headers

Scroll and dispatch events to make Quora fire a questions request naturally:

```javascript
window.scrollTo(0, document.body.scrollHeight);
window.dispatchEvent(new Event('scroll'));
document.dispatchEvent(new Event('scroll'));
'Triggered'
```

Then verify headers were captured:

```javascript
const raw = localStorage.getItem('_gql_capture3');
raw ? JSON.parse(raw)[0].url : 'not captured yet'
```

Expected: URL containing `UserProfileQuestionsList_Questions_Query`

If not captured after 5 seconds, scroll again or check
`mcp__claude-in-chrome__read_network_requests` for `gql_para_POST` entries.

---

## Step 5 — Discover the query hash

The hash is embedded in the captured request body:

```javascript
const entry = JSON.parse(localStorage.getItem('_gql_capture3'))[0];
JSON.parse(entry.body).extensions.hash
```

This should return a 64-character hex string. **Record it** — it changes with
each Quora frontend deployment. The most recently known hash is:

```
3af90ad8f3a28fee7837565ab4e334ad41ab03b1ae32c773cc233d7e4099d7aa
```

If the captured hash differs from the above, use the new one in Step 6.

---

## Step 6 — Find the user's numeric uid

```javascript
const entry = JSON.parse(localStorage.getItem('_gql_capture3'))[0];
JSON.parse(entry.body).variables.uid
```

For Miguel Paraz this is `229089`. For other users, find it here or in the
page HTML (`grep` for `"uid":` in the page source).

---

## Step 7 — Inject the extraction loop

Replace `HASH`, `UID`, and `OUTPUT_PATH` as needed. This runs as a background
script and saves progress to `localStorage` every 5 API calls.

```javascript
const s = document.createElement('script');
s.textContent = `
(async function extractQuestions() {
  const headers = JSON.parse(localStorage.getItem('_gql_capture3'))[0].headers;
  const HASH = '3af90ad8f3a28fee7837565ab4e334ad41ab03b1ae32c773cc233d7e4099d7aa';
  const QUERY = 'UserProfileQuestionsList_Questions_Query';
  const UID   = 229089;

  function parseTitle(titleJson) {
    try {
      const p = JSON.parse(titleJson);
      return p.sections.map(s => s.spans.map(sp => sp.text||'').join('')).join(' ').trim();
    } catch(e) { return String(titleJson).trim(); }
  }

  const all = [];
  let cursor = null;
  let hasNextPage = true;
  let page = 0;

  localStorage.setItem('_api_meta', JSON.stringify({ status:'running', total:0, chunks:0, done:false, page:0 }));

  while (hasNextPage) {
    try {
      const resp = await fetch('/graphql/gql_para_POST?q=' + QUERY, {
        method: 'POST', credentials: 'include', headers,
        body: JSON.stringify({
          queryName: QUERY,
          variables: { uid: UID, first: 100, after: cursor },
          extensions: { hash: HASH }
        })
      });
      const data = await resp.json();
      const conn = data.data.user.recentPublicQuestionsConnection;
      if (!conn?.edges) {
        localStorage.setItem('_api_error', JSON.stringify({ page, cursor }));
        break;
      }
      for (const edge of conn.edges) {
        all.push([
          'https://www.quora.com' + edge.node.url,
          parseTitle(edge.node.title)
        ]);
      }
      hasNextPage = conn.pageInfo.hasNextPage;
      cursor = conn.pageInfo.endCursor;
      page++;
      if (page % 5 === 0 || !hasNextPage) {
        const cs = 500, chunks = Math.ceil(all.length / cs);
        for (let i = 0; i < chunks; i++)
          localStorage.setItem('_api_chunk_' + i, JSON.stringify(all.slice(i*cs, (i+1)*cs)));
        localStorage.setItem('_api_meta', JSON.stringify({
          status: hasNextPage ? 'running' : 'done',
          total: all.length, chunks, done: !hasNextPage, page
        }));
      }
      await new Promise(r => setTimeout(r, 150));
    } catch(err) {
      localStorage.setItem('_api_error', JSON.stringify({ page, cursor, error: err.message }));
      break;
    }
  }
  console.log('[Q EXTRACT] Done:', all.length);
})();
`;
document.head.appendChild(s);
'Extraction loop injected'
```

---

## Step 8 — Monitor progress

Poll `_api_meta` every 90–270 seconds (stay under 270s to keep prompt cache warm):

```javascript
JSON.stringify(JSON.parse(localStorage.getItem('_api_meta')))
```

Expected progress: ~20 questions per API call, ~150ms per call initially,
slowing to ~3s per call after ~400 calls (Quora response time increases).
Total for ~14,700 questions: ~20–30 minutes end-to-end.

Also check for errors:
```javascript
localStorage.getItem('_api_error')
```

If an error appears mid-run due to localStorage quota, see **Troubleshooting**.

Done when `status === "done"`.

---

## Step 9 — Save to file via AppleScript

Once `status === "done"`, run this Python script from the terminal. It reads
localStorage via AppleScript (Chrome must have "Allow JavaScript from Apple
Events" enabled under View → Developer):

```python
import subprocess, json
from datetime import date

def applescript_js(js):
    r = subprocess.run(
        ['osascript', '-e',
         f'tell application "Google Chrome" to return execute tab 1 of window 1 javascript "{js}"'],
        capture_output=True, text=True, timeout=30)
    return r.stdout.strip()

meta = json.loads(applescript_js('localStorage.getItem(\\"_api_meta\\")'))
print(f"Total: {meta['total']}, chunks: {meta['chunks']}")

all_entries = []
for i in range(meta['chunks']):
    chunk = json.loads(applescript_js(f'localStorage.getItem(\\"_api_chunk_{i}\\")'))
    all_entries.extend(chunk)

today = date.today().strftime('%Y-%m-%d')
header = (
    f"# Miguel Paraz - Quora Questions\n\n"
    f"Collected {today} via Quora GraphQL API (`UserProfileQuestionsList_Questions_Query`).\n"
    f"Total: {len(all_entries)} questions\n\n---\n\n"
)
lines = '\n'.join(f'- [{title}]({url})' for url, title in all_entries)
with open('miguel_paraz_quora_questions.md', 'w') as f:
    f.write(header + lines + '\n')
print(f"Written {len(all_entries)} questions")
```

---

## Troubleshooting

### localStorage quota exceeded

Symptom: `_api_error` contains `"exceeded the quota"`.

Fix:
1. Save current chunks to file immediately (run Step 9 with partial data).
2. Record the `nextCursor` value from `_api_meta`.
3. Clear old chunks:
   ```javascript
   for (let i = 0; i < 40; i++) localStorage.removeItem('_api_chunk_' + i);
   // Also remove other large keys from previous sessions
   ['_quora_chunk_0','_quora_meta','_gql_test_resp'].forEach(k => localStorage.removeItem(k));
   ```
4. Restart extraction from the saved cursor (modify Step 7 to set `cursor = '<saved_cursor>'` before the loop).
5. Append new results to the existing file.

### Headers expired / 401 responses

The `Quora-Turnstile-Token` is short-lived. If API calls start returning errors:
1. Reload the page (this generates a fresh token).
2. Repeat Steps 2–4 to re-capture headers.
3. Restart extraction.

### Hash changed (new Quora deploy)

If the API returns 418 or a "query not found" error:
1. The hash in Step 5 will differ from the hardcoded fallback.
2. Use the freshly captured hash from Step 5 instead.
3. Update the hardcoded hash in this runbook.

---

## Key constants (as of 2026-05-22)

| Constant | Value |
|----------|-------|
| Query name | `UserProfileQuestionsList_Questions_Query` |
| Hash | `3af90ad8f3a28fee7837565ab4e334ad41ab03b1ae32c773cc233d7e4099d7aa` |
| uid (Miguel Paraz) | `229089` |
| Items per API call | 20 (server-enforced despite `first: 100`) |
| Cursor type | Numeric offset string (`"0"`, `"19"`, `"39"`, …) |
| Connection field | `data.user.recentPublicQuestionsConnection` |
| Title field | `edges[].node.title` (JSON rich text — parse with `parseTitle()`) |
| URL field | `edges[].node.url` (relative — prepend `https://www.quora.com`) |

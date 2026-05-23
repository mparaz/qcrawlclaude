# Runbook: Download Quora Answers via GraphQL API

Operational guide for Claude to extract all answers written by a Quora user.
Run inside an authenticated Chrome browser session using the Claude-in-Chrome extension.

> **Key difference from RUNBOOK_QUESTIONS.md:** With ~13,000+ answers the
> localStorage quota (~5–10 MB) is exceeded in a single pass. This runbook
> uses a **batch-of-2000** strategy: extract 2,000 answers, save to file,
> clear localStorage, repeat from the saved cursor.

---

## Prerequisites

- Chrome tab open and logged in to Quora
- Claude-in-Chrome MCP extension active
- Tab ID known (get via `mcp__claude-in-chrome__tabs_context_mcp`)

---

## Step 1 — Navigate to the answers page

```
https://www.quora.com/profile/<username>/answers
```

Tool: `mcp__claude-in-chrome__navigate`

Wait for page to load (title should show username + "Answers").

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
isolated world). Stores the last 5 captured GraphQL requests in `localStorage`.

```javascript
const s = document.createElement('script');
s.textContent = `
  window._interceptorActive = true;
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
      localStorage.setItem('_gql_capture3', JSON.stringify(prev.slice(0,5)));
    }
    return _of.apply(this, args);
  };
`;
document.head.appendChild(s);
'Interceptor injected'
```

---

## Step 4 — Trigger a real API request to capture headers

The answers page uses an `IntersectionObserver` for lazy loading, so both a
`scrollTo` and an explicit `scroll` event dispatch are needed:

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

Expected: URL containing `UserProfileAnswersMostRecent_RecentAnswers_Query`

If not captured after 5 seconds, scroll again. The answers page scroll height
may be short (~1100px) even when content is loading — the `dispatchEvent` call
is what triggers the IntersectionObserver.

---

## Step 5 — Discover the query hash

```javascript
const entries = JSON.parse(localStorage.getItem('_gql_capture3'));
const ansEntry = entries.find(e => e.url.includes('RecentAnswers'));
ansEntry ? JSON.parse(ansEntry.body).extensions.hash : 'answers query not captured yet'
```

This should return a 64-character hex string. **Record it** — it changes with
each Quora frontend deployment. The most recently known hash is:

```
387718c70387d11d611f1aef67066ee1b645732539a4a48f593a2458a6bd11ae
```

If the captured hash differs, use the new one in Step 6.

---

## Step 6 — Find the user's numeric uid

```javascript
const entries = JSON.parse(localStorage.getItem('_gql_capture3'));
const ansEntry = entries.find(e => e.url.includes('RecentAnswers'));
ansEntry ? JSON.parse(ansEntry.body).variables.uid : 'not found'
```

For Miguel Paraz this is `229089`.

---

## Step 7 — Clear old localStorage data

Before starting extraction, remove any keys from previous sessions to avoid
quota conflicts:

```javascript
// Remove old answer chunks (up to 30)
for (let i = 0; i < 30; i++) localStorage.removeItem('_ans_chunk_' + i);
// Remove old question/scroll extraction keys
for (let i = 0; i < 40; i++) {
  localStorage.removeItem('_api_chunk_' + i);
  localStorage.removeItem('_quora_chunk_' + i);
}
['_api_meta','_api_error','_ans_meta','_quora_meta','_gql_test_resp','_acbq_test','_acbq_test2'].forEach(k => localStorage.removeItem(k));
'Cleared'
```

---

## Step 8 — Inject the batch extraction loop

This script extracts up to `maxEntries` answers starting from `startCursor`.
For the first batch use `startCursor = null`. For subsequent batches paste the
cursor saved in Step 10.

Replace `HASH` and `UID` as needed.

```javascript
const s = document.createElement('script');
s.textContent = `
(async function extractAnswersBatch(startCursor, maxEntries) {
  const headers = JSON.parse(localStorage.getItem('_gql_capture3'))[0].headers;
  const HASH  = '387718c70387d11d611f1aef67066ee1b645732539a4a48f593a2458a6bd11ae';
  const QUERY = 'UserProfileAnswersMostRecent_RecentAnswers_Query';
  const UID   = 229089;

  function parseTitle(t) {
    try {
      const p = JSON.parse(t);
      return p.sections.map(s => s.spans.map(sp => sp.text||'').join('')).join(' ').trim();
    } catch(e) { return String(t).trim(); }
  }

  const all = [];
  let cursor = startCursor;
  let hasNextPage = true;
  let page = 0;

  localStorage.setItem('_ans_meta', JSON.stringify({
    status: 'running', total: 0, chunks: 0, done: false, page: 0, nextCursor: cursor, allDone: false
  }));

  while (hasNextPage && all.length < maxEntries) {
    try {
      const resp = await fetch('/graphql/gql_para_POST?q=' + QUERY, {
        method: 'POST', credentials: 'include', headers,
        body: JSON.stringify({
          queryName: QUERY,
          variables: { uid: UID, first: 100, after: cursor, answerFilterTid: null },
          extensions: { hash: HASH }
        })
      });
      const data = await resp.json();
      const conn = data.data.user.recentPublicAndPinnedAnswersConnection;
      if (!conn?.edges) {
        localStorage.setItem('_ans_error', JSON.stringify({ page, cursor, resp: JSON.stringify(data).substring(0,200) }));
        break;
      }
      for (const e of conn.edges) {
        all.push([
          'https://www.quora.com' + e.node.logUrl.replace('/log', ''),
          parseTitle(e.node.question?.title || '')
        ]);
      }
      hasNextPage = conn.pageInfo.hasNextPage;
      cursor = conn.pageInfo.endCursor;
      page++;

      const batchDone = !hasNextPage || all.length >= maxEntries;
      if (page % 5 === 0 || batchDone) {
        const cs = 500, chunks = Math.ceil(all.length / cs);
        for (let i = 0; i < chunks; i++)
          localStorage.setItem('_ans_chunk_' + i, JSON.stringify(all.slice(i*cs, (i+1)*cs)));
        localStorage.setItem('_ans_meta', JSON.stringify({
          status: batchDone ? 'batch_done' : 'running',
          total: all.length, chunks, done: !hasNextPage,
          page, nextCursor: cursor, allDone: !hasNextPage
        }));
      }
      await new Promise(r => setTimeout(r, 150));
    } catch(err) {
      localStorage.setItem('_ans_error', JSON.stringify({ page, cursor, error: err.message }));
      break;
    }
  }
  console.log('[ANS EXTRACT] Batch done:', all.length, 'cursor:', cursor);
})(null, 2000);
`;
document.head.appendChild(s);
'Extraction batch injected'
```

---

## Step 9 — Monitor batch progress

Poll `_ans_meta` every 90–270 seconds (stay under 270s to keep prompt cache warm):

```javascript
JSON.stringify(JSON.parse(localStorage.getItem('_ans_meta')))
```

Also check for errors:

```javascript
localStorage.getItem('_ans_error')
```

Expected progress: ~20 answers per API call, ~150ms per call initially,
slowing to ~3s per call after ~400 total pages.

Done when `status === "batch_done"`. **Record the `nextCursor` value** before
proceeding — you need it to start the next batch if `allDone` is `false`.

---

## Step 10 — Save batch to file via AppleScript

Run this Python script from the terminal after each batch completes. It reads
localStorage via AppleScript (Chrome must have "Allow JavaScript from Apple
Events" enabled under View → Developer).

On the first batch, pass `output_path` and `mode='w'`. For subsequent batches,
use `mode='a'` to append.

```python
import subprocess, json
from datetime import date

def applescript_js(js):
    r = subprocess.run(
        ['osascript', '-e',
         f'tell application "Google Chrome" to return execute tab 1 of window 1 javascript "{js}"'],
        capture_output=True, text=True, timeout=30)
    return r.stdout.strip()

meta = json.loads(applescript_js('localStorage.getItem(\\"_ans_meta\\")'))
print(f"Status: {meta['status']}, total: {meta['total']}, allDone: {meta.get('allDone')}")
print(f"Next cursor: {meta.get('nextCursor')}")

all_entries = []
for i in range(meta['chunks']):
    chunk = json.loads(applescript_js(f'localStorage.getItem(\\"_ans_chunk_{i}\\")'))
    all_entries.extend(chunk)

output_path = 'miguel_paraz_quora_answers.md'
mode = 'w'  # use 'a' for subsequent batches

if mode == 'w':
    today = date.today().strftime('%Y-%m-%d')
    header = (
        f"# Miguel Paraz - Quora Answers\n\n"
        f"Collected {today} via Quora GraphQL API (`UserProfileAnswersMostRecent_RecentAnswers_Query`).\n\n---\n\n"
    )
    with open(output_path, 'w') as f:
        f.write(header)

lines = '\n'.join(f'- [{title}]({url})' for url, title in all_entries)
with open(output_path, 'a') as f:
    f.write(lines + '\n')

print(f"Written {len(all_entries)} answers to {output_path} (mode={mode})")
```

---

## Step 11 — Repeat for remaining batches

If `allDone` is `false`, there are more answers to fetch. Repeat Steps 7–10:

1. Run Step 7 (clear old chunks).
2. Run Step 8 with the saved `nextCursor` and `maxEntries = 2000`:
   ```javascript
   })('{PASTE_NEXT_CURSOR_HERE}', 2000);
   ```
3. Monitor (Step 9) until `status === "batch_done"`.
4. Save with `mode = 'a'` (Step 10).

Continue until `allDone === true`.

---

## Step 12 — Add final count to file header

Once all batches are done, update the file header with the total count:

```python
with open('miguel_paraz_quora_answers.md', 'r') as f:
    content = f.read()

total = content.count('\n- [')
content = content.replace('---\n\n', f'Total: {total} answers\n\n---\n\n', 1)

with open('miguel_paraz_quora_answers.md', 'w') as f:
    f.write(content)

print(f"Updated header with total: {total}")
```

---

## Troubleshooting

### Headers captured but wrong query (questions instead of answers)

The `_gql_capture3` list may contain a questions query from a previous session.
Step 5 filters by URL — if no answers entry is found, clear the capture list
and re-trigger:

```javascript
localStorage.removeItem('_gql_capture3');
window.scrollTo(0, document.body.scrollHeight);
window.dispatchEvent(new Event('scroll'));
'Re-triggered'
```

### localStorage quota exceeded mid-batch

Symptom: `_ans_error` contains `"exceeded the quota"`.

Fix:
1. Read the current `nextCursor` from `_ans_meta` before the error key is set.
2. Save whatever chunks completed (run Step 10).
3. Clear all chunks (Step 7) and restart the batch from the last good cursor.

If quota is hit consistently before 2,000 entries, reduce `maxEntries` to 1,000.

### Headers expired / 401 or 403 responses

The `Quora-Turnstile-Token` is short-lived (~minutes). If API calls start
returning errors:
1. Reload the answers page (this generates a fresh token).
2. Repeat Steps 2–4 to re-capture headers.
3. Restart the current batch from the last `nextCursor`.

### Interceptor not capturing answers query

If the page was already fully loaded before the interceptor was injected, no
new GraphQL calls will fire. Fix: reload the page, then immediately run Steps
2–4 before the page has finished loading its initial data.

### Hash changed (new Quora deploy)

If the API returns 418 or a "query not found" error:
1. Find the new hash from the captured `_gql_capture3` entry (Step 5).
2. Use it in Step 8 instead of the hardcoded value.
3. Update the hardcoded hash in this runbook.

---

## Key constants (as of 2026-05-23)

| Constant | Value |
|----------|-------|
| Query name | `UserProfileAnswersMostRecent_RecentAnswers_Query` |
| Hash | `387718c70387d11d611f1aef67066ee1b645732539a4a48f593a2458a6bd11ae` |
| uid (Miguel Paraz) | `229089` |
| Items per API call | 20 (server-enforced despite `first: 100`) |
| Cursor type | Numeric offset string (`"0"`, `"19"`, `"39"`, …) |
| Connection field | `data.user.recentPublicAndPinnedAnswersConnection` |
| Title field | `edges[].node.question.title` (JSON rich text — parse with `parseTitle()`) |
| URL field | `edges[].node.logUrl` (strip `/log` suffix, prepend `https://www.quora.com`) |
| Recommended batch size | 2,000 (avoids localStorage quota at ~13,000 total answers) |
| Total answers (Miguel Paraz) | 13,295 (as of 2026-05-22) |
| Estimated time | ~20–30 minutes per 2,000 answers; ~2–3 hours total |

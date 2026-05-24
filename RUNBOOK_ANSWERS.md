# Runbook: Download Quora Answers via GraphQL API

Operational guide for Claude to extract all answers written by a Quora user,
including full answer text. Run inside an authenticated Chrome browser session
using the Claude-in-Chrome extension.

This runbook covers two passes:

- **Pass 1** — collect answer list (URL, question title, `aid`) using
  `UserProfileAnswersMostRecent_RecentAnswers_Query`
- **Pass 2** — fetch full answer body for each `aid` using
  `AnswerComponentBaseQuery`

> **localStorage quota note:** With ~13,000+ answers Pass 1 uses a
> **batch-of-2000** strategy. Pass 2 answer bodies are larger (~1–5 KB each)
> so use batches of 200 or process all at once for users with fewer answers.

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

## Step 8 — Inject the Pass 1 batch extraction loop

This script extracts up to `maxEntries` answers starting from `startCursor`.
For the first batch use `startCursor = null`. For subsequent batches paste the
cursor saved in Step 10.

Each entry is stored as `[url, title, aid]` — the `aid` is required for Pass 2.

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
          parseTitle(e.node.question?.title || ''),
          e.node.aid
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

## Step 10 — Save Pass 1 batch to file via AppleScript

Run this Python script from the terminal after each batch completes. It reads
localStorage via AppleScript (Chrome must have "Allow JavaScript from Apple
Events" enabled under View → Developer).

Each entry is `[url, title, aid]`. The script writes a URL list markdown file
and also saves a JSON file of all entries (needed for Pass 2).

On the first batch use `mode='w'`. For subsequent batches use `mode='a'`.

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

username = 'alan-kay'          # change as needed
output_path = f'{username}_quora_answers.md'
aids_path   = f'{username}_aids.json'
mode = 'w'  # use 'a' for subsequent batches

if mode == 'w':
    today = date.today().strftime('%Y-%m-%d')
    header = (
        f"# {username.title()} - Quora Answers\n\n"
        f"Collected {today} via Quora GraphQL API (`UserProfileAnswersMostRecent_RecentAnswers_Query`).\n\n---\n\n"
    )
    with open(output_path, 'w') as f:
        f.write(header)

# Write URL list (url and title only — aid is stripped for readability)
lines = '\n'.join(f'- [{entry[1]}]({entry[0]})' for entry in all_entries)
with open(output_path, 'a') as f:
    f.write(lines + '\n')
print(f"Written {len(all_entries)} answers to {output_path} (mode={mode})")

# Save full [url, title, aid] triples for Pass 2
aids_mode = 'w' if mode == 'w' else 'r+'
existing = []
if mode == 'a':
    with open(aids_path) as f:
        existing = json.load(f)
with open(aids_path, 'w') as f:
    json.dump(existing + all_entries, f)
print(f"Saved {len(existing) + len(all_entries)} entries to {aids_path}")
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
with open('alan-kay_quora_answers.md', 'r') as f:
    content = f.read()

total = content.count('\n- [')
content = content.replace('---\n\n', f'Total: {total} answers\n\n---\n\n', 1)

with open('alan-kay_quora_answers.md', 'w') as f:
    f.write(content)

print(f"Updated header with total: {total}")
```

---

## Pass 2 — Fetch Full Answer Bodies

Once Pass 1 is complete (`allDone: true`) and `<username>_aids.json` exists,
run Pass 2 to fetch the full text of each answer.

### Step P1 — Stage aids from Pass 1 into localStorage

Reads the `_ans_chunk_*` keys still in localStorage and consolidates them into
a single `_pass2_entries` key for the body-fetch loop.

```javascript
// Clear old body chunks
for (let i = 0; i < 40; i++) localStorage.removeItem('_body_chunk_' + i);
localStorage.removeItem('_body_meta');
localStorage.removeItem('_body_error');

// Consolidate Pass 1 chunks into _pass2_entries
const meta = JSON.parse(localStorage.getItem('_ans_meta'));
const all = [];
for (let i = 0; i < meta.chunks; i++) {
  const chunk = JSON.parse(localStorage.getItem('_ans_chunk_' + i));
  all.push(...chunk);
}
localStorage.setItem('_pass2_entries', JSON.stringify(all));
'Ready: ' + all.length + ' entries staged for Pass 2'
```

If Pass 1 localStorage was already cleared, load from the JSON file instead:

```python
import subprocess, json

def applescript_js(js):
    r = subprocess.run(
        ['osascript', '-e',
         f'tell application "Google Chrome" to return execute tab 1 of window 1 javascript "{js}"'],
        capture_output=True, text=True, timeout=30)
    return r.stdout.strip()

with open('alan-kay_aids.json') as f:
    entries = json.load(f)

# Write in chunks to avoid AppleScript string size limits
chunk_size = 200
for i in range(0, len(entries), chunk_size):
    chunk_json = json.dumps(entries[i:i+chunk_size]).replace('"', '\\"')
    applescript_js(f'localStorage.setItem(\\"_pass2_chunk_{i//chunk_size}\\", \\"{chunk_json}\\")')

applescript_js(f'localStorage.setItem(\\"_pass2_meta\\", \\"{{\\"chunks\\":{(len(entries)+chunk_size-1)//chunk_size},\\"total\\":{len(entries)}}}\\")')
print(f"Staged {len(entries)} entries")
```

### Step P2 — Inject the body fetch loop

Replace `START_IDX` (0 for first run) and `BATCH_SIZE` (200 is safe; use total
count if fewer than ~500 answers). The loop reads from `_pass2_entries`.

```javascript
const s = document.createElement('script');
s.textContent = `
(async function fetchBodies(startIdx, batchSize) {
  const headers = JSON.parse(localStorage.getItem('_gql_capture3'))[0].headers;
  const HASH  = '28e392de2fd885ba6962146a8bac8e6ec7c978af89c0d70e56695284efbe8a2b';
  const QUERY = 'AnswerComponentBaseQuery';
  const entries = JSON.parse(localStorage.getItem('_pass2_entries'));
  const end = Math.min(startIdx + batchSize, entries.length);

  function parseContent(t) {
    try {
      const p = JSON.parse(t);
      return p.sections.map(s => s.spans.map(sp => sp.text||'').join('')).join('\\n').trim();
    } catch(e) { return String(t).trim(); }
  }

  const results = [];
  localStorage.setItem('_body_meta', JSON.stringify({
    status: 'running', done: 0, total: end - startIdx, startIdx, endIdx: end,
    allDone: end >= entries.length, chunks: 0
  }));

  for (let i = startIdx; i < end; i++) {
    const [url, title, aid] = entries[i];
    try {
      const resp = await fetch('/graphql/gql_para_POST?q=' + QUERY, {
        method: 'POST', credentials: 'include', headers,
        body: JSON.stringify({
          queryName: QUERY,
          variables: { aid, showActionBarForLoggedOut: false, skipFooter: false, skipMetabar: false },
          extensions: { hash: HASH }
        })
      });
      const data = await resp.json();
      const body = data?.data?.answer?.content
        ? parseContent(data.data.answer.content) : '';
      results.push([url, title, body]);
    } catch(err) {
      results.push([url, title, '']);
      localStorage.setItem('_body_error', JSON.stringify({ idx: i, aid, error: err.message }));
    }

    const done = i - startIdx + 1;
    if (done % 50 === 0 || i === end - 1) {
      const cs = 100, chunks = Math.ceil(results.length / cs);
      for (let c = 0; c < chunks; c++)
        localStorage.setItem('_body_chunk_' + c, JSON.stringify(results.slice(c*cs, (c+1)*cs)));
      localStorage.setItem('_body_meta', JSON.stringify({
        status: i === end - 1 ? 'batch_done' : 'running',
        done, total: end - startIdx, startIdx, endIdx: end,
        allDone: end >= entries.length, chunks
      }));
    }
    await new Promise(r => setTimeout(r, 200));
  }
  console.log('[BODY EXTRACT] Done:', results.length);
})(0, 9999);
`;
document.head.appendChild(s);
'Pass 2 body extraction injected'
```

### Step P3 — Monitor Pass 2 progress

```javascript
JSON.stringify(JSON.parse(localStorage.getItem('_body_meta')))
```

```javascript
localStorage.getItem('_body_error')
```

Expected rate: ~200ms per answer. 716 answers ≈ 2.5 minutes.

Done when `status === "batch_done"`.

### Step P4 — Save bodies to file via AppleScript

```python
import subprocess, json
from datetime import date

def applescript_js(js):
    r = subprocess.run(
        ['osascript', '-e',
         f'tell application "Google Chrome" to return execute tab 1 of window 1 javascript "{js}"'],
        capture_output=True, text=True, timeout=60)
    return r.stdout.strip()

meta = json.loads(applescript_js('localStorage.getItem(\\"_body_meta\\")'))
print(f"Status: {meta['status']}, done: {meta['done']}, chunks: {meta['chunks']}")

all_entries = []
for i in range(meta['chunks']):
    chunk = json.loads(applescript_js(f'localStorage.getItem(\\"_body_chunk_{i}\\")'))
    all_entries.extend(chunk)

today = date.today().strftime('%Y-%m-%d')
username = 'alan-kay'   # change as needed
output_path = f'{username}_quora_answer_bodies.md'

header = (
    f"# {username.title()} - Quora Answer Bodies\n\n"
    f"Collected {today} via Quora GraphQL API (`AnswerComponentBaseQuery`).\n"
    f"Total: {len(all_entries)} answers\n\n---\n\n"
)

sections = []
for url, title, body in all_entries:
    paragraphs = '\n\n'.join(p for p in body.split('\n') if p.strip())
    sections.append(f"## [{title}]({url})\n\n{paragraphs}\n\n---")

mode = 'w'  # use 'a' and skip header for subsequent batches
with open(output_path, mode) as f:
    if mode == 'w':
        f.write(header)
    f.write('\n'.join(sections) + '\n')

print(f"Written {len(all_entries)} bodies to {output_path}")
```

### Step P5 — Repeat for large user counts

If the user has more answers than fit in one Pass 2 batch (quota exceeded),
save the current batch, clear body chunks, and re-run Step P2 with
`startIdx = endIdx` from the last `_body_meta`:

```javascript
})(700, 200);   // startIdx = where last batch ended
```

Append to the output file with `mode = 'a'` in Step P4 (and skip the header).

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

## Key constants (as of 2026-05-24)

### Pass 1 — Answer list

| Constant | Value |
|----------|-------|
| Query name | `UserProfileAnswersMostRecent_RecentAnswers_Query` |
| Hash | `387718c70387d11d611f1aef67066ee1b645732539a4a48f593a2458a6bd11ae` |
| uid (Miguel Paraz) | `229089` |
| uid (Alan Kay) | `117344100` |
| Items per API call | 20 (server-enforced despite `first: 100`) |
| Cursor type | Numeric offset string (`"0"`, `"19"`, `"39"`, …) |
| Connection field | `data.user.recentPublicAndPinnedAnswersConnection` |
| Title field | `edges[].node.question.title` (JSON rich text — parse with `parseTitle()`) |
| URL field | `edges[].node.logUrl` (strip `/log` suffix, prepend `https://www.quora.com`) |
| Aid field | `edges[].node.aid` (integer — required for Pass 2) |
| Recommended batch size | 2,000 (avoids localStorage quota at ~13,000 total answers) |
| Total answers (Miguel Paraz) | 13,295 (as of 2026-05-22) |
| Total answers (Alan Kay) | 716 (as of 2026-05-24) |
| Estimated time (Pass 1) | ~20–30 min per 2,000 answers |

### Pass 2 — Answer bodies

| Constant | Value |
|----------|-------|
| Query name | `AnswerComponentBaseQuery` |
| Hash | `28e392de2fd885ba6962146a8bac8e6ec7c978af89c0d70e56695284efbe8a2b` |
| Webpack chunk | `-4-ans_frontend-relay-rspack-query-AnswerComponentBaseQuery-27-11da9f4a783a92ac.webpack` |
| Variables | `{ aid, showActionBarForLoggedOut: false, skipFooter: false, skipMetabar: false }` |
| Content field | `data.answer.content` (JSON rich text — parse with `parseContent()`) |
| Section separator | `'\n'` (use newline, not space, to preserve paragraph breaks) |
| Rate | 1 call per answer, 200ms delay |
| Recommended batch size | 200 per localStorage save cycle |
| Estimated time (Pass 2) | ~2.5 min per 700 answers; scales linearly |

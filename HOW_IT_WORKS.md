# How Quora Question Extraction Works

This documents the technique used to extract all questions asked by a Quora user
via browser automation, without using Quora's API.

---

## The Problem

Quora does not provide a public API for downloading user content. The questions
page (`/profile/<user>/questions`) uses **infinite scroll** — content loads
dynamically as you scroll down, rather than being paginated. With 14.7K questions,
manual scrolling is impractical.

---

## Architecture Overview

Three independent processes collaborate:

```
┌─────────────────────────────────────────────────┐
│  Chrome (Quora page)                            │
│                                                  │
│  ① Scroll loop (setInterval 800ms)              │
│     scrollTo(0, document.body.scrollHeight)     │
│                                                  │
│  ② Extraction (on each scroll)                  │
│     Reads <a href> links → window._quoraQuestions│
│                                                  │
│  ③ Auto-save (setInterval 5s)                   │
│     Writes chunks → localStorage                │
└─────────────────┬───────────────────────────────┘
                  │ AppleScript reads localStorage
                  ▼
┌─────────────────────────────────────────────────┐
│  Python process (/tmp/quora_autosave.py)        │
│                                                  │
│  Every 60s: osascript → Chrome JS →             │
│  read localStorage chunks →                     │
│  write markdown file                            │
└─────────────────┬───────────────────────────────┘
                  │ writes
                  ▼
    miguel_paraz_quora_questions.md
```

---

## Step-by-Step

### 1. Navigate to the Questions Page

```
https://www.quora.com/profile/<username>/questions
```

This page shows questions in reverse-chronological order with infinite scroll.

### 2. Inject the Scroll + Extraction Loop (JavaScript)

Injected via Claude's browser extension (`mcp__claude-in-chrome__javascript_tool`):

```javascript
window._quoraQuestions = new Map(); // url → title, deduped

function extractQuestions() {
  Array.from(document.querySelectorAll('a[href]')).forEach(a => {
    const url = a.href;
    const title = a.innerText.trim();
    // Match question URLs: /Question-Slug or /unanswered/Question-Slug
    if (url.match(/https:\/\/www\.quora\.com\/(?:unanswered\/)?[A-Za-z][^/?#]{10,}$/) &&
        !url.includes('/profile/') && !url.includes('/topic/') &&
        title.length > 5 && title.includes(' ') &&
        !window._quoraQuestions.has(url)) {
      window._quoraQuestions.set(url, title);
    }
  });
}

// Scroll every 800ms, stop after 15 consecutive scrolls with no new questions
window._scrollInterval = setInterval(() => {
  window.scrollTo(0, document.body.scrollHeight);
  extractQuestions();
  // ... stop condition
}, 800);
```

**Why a Map?** Quora renders each question twice (once for the title link, once
for the answer count link), so using a Map keyed by URL deduplicates automatically.

**URL patterns recognised:**
- `https://www.quora.com/Why-is-the-sky-blue` — answered questions
- `https://www.quora.com/unanswered/Why-is-the-sky-blue` — unanswered questions

### 3. Bridge Data Out via localStorage

The browser extension runs in an **isolated JavaScript world** — variables set
there are invisible to the main page context. However, `localStorage` is shared
between isolated worlds and the main page.

AppleScript's `execute javascript` runs in the **main world**, so `window._quoraQuestions`
is not directly readable via AppleScript. The solution: write to `localStorage`.

```javascript
// Every 100 new questions, chunk and save to localStorage
const chunkSize = 500;
const chunks = Math.ceil(entries.length / chunkSize);
for (let i = 0; i < chunks; i++) {
  localStorage.setItem('_quora_chunk_' + i,
    JSON.stringify(entries.slice(i * chunkSize, (i + 1) * chunkSize)));
}
localStorage.setItem('_quora_meta',
  JSON.stringify({ total: entries.length, chunks, done: scrollDone }));
```

Chunks of 500 keep each localStorage value well under the per-key size limit.

### 4. Python Auto-Save via AppleScript

A Python process runs every 60 seconds and reads localStorage via AppleScript:

```python
def applescript_js(js):
    result = subprocess.run(
        ['osascript', '-e',
         f'tell application "Google Chrome" to return '
         f'execute tab 1 of window 1 javascript "{js}"'],
        capture_output=True, text=True)
    return result.stdout.strip()

meta = json.loads(applescript_js('localStorage.getItem(\\"_quora_meta\\")'))
entries = []
for i in range(meta['chunks']):
    chunk = json.loads(applescript_js(f'localStorage.getItem(\\"_quora_chunk_{i}\\")'))
    entries.extend(chunk)

# Write markdown
with open('miguel_paraz_quora_questions.md', 'w') as f:
    f.write('# Questions\n\n')
    f.write('\n'.join(f'- [{title}]({url})' for url, title in entries))
```

**Prerequisite:** Chrome must have "Allow JavaScript from Apple Events" enabled
(`View → Developer → Allow JavaScript from Apple Events`).

### 5. Claude Code Monitors via ScheduleWakeup

Claude Code itself checks health every ~4.5 minutes using `ScheduleWakeup`:

```
ScheduleWakeup(delaySeconds=270, prompt="Check progress...")
```

270 seconds stays within the Anthropic prompt cache's 5-minute TTL window,
keeping context retrieval fast and cheap. On each wakeup, Claude checks:
- Is the Python process still alive? (`pgrep`)
- Is the file growing? (`wc -l`)
- Has the scroll finished? (`window._scrollDone`)

---

## Why Not Use the Quora API?

Quora does not offer a public API for user content. Alternatives considered:

| Approach | Problem |
|---|---|
| Quora API | Does not exist publicly |
| Quora Data Export | Only available to the account holder, ZIP download |
| `fetch()` from page to local server | Blocked: HTTPS → HTTP mixed content |
| WebSocket to local server | Blocked: Chrome mixed content policy |
| Chrome remote debugging (CDP) | Requires `--remote-debugging-port` flag at launch |
| Clipboard (`pbpaste`) | Works but blocks user clipboard during save |
| **localStorage + AppleScript** | ✅ Works, no clipboard use, no external dependencies |

---

## Limitations

- **Speed:** ~100 questions/minute at 800ms scroll intervals. 14.7K questions
  takes ~2.5 hours.
- **Coverage:** Quora's infinite scroll may not surface all questions if the
  page is very long. In practice we see diminishing returns past ~10K items.
- **Session dependency:** All data lives in the browser tab's memory
  (`window._quoraQuestions`). If the tab is closed or navigated away, progress
  is lost (though the last saved file checkpoint is preserved).
- **Quora login required:** The profile questions page requires being logged in
  to see all questions.
- **Rate:** Quora may throttle or show a CAPTCHA if scroll speed is too
  aggressive. 800ms intervals have proven stable in practice.

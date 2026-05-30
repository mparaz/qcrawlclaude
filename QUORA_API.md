# Quora Private Web API

Documents the internal GraphQL API used by Quora's web frontend to load user
profile questions, answers, and full answer text. Discovered by intercepting
browser network traffic.

> **Note:** This is an undocumented, private API. It may change without notice.
> Use for personal data access only and respect Quora's terms of service.

---

## Queries

| Query | Purpose |
|-------|---------|
| [`UserProfileQuestionsList_Questions_Query`](#questions-list) | All questions asked by a user (paginated) |
| [`UserProfileAnswersMostRecent_RecentAnswers_Query`](#answers-list) | All answers by a user (paginated) |
| [`AnswerComponentBaseQuery`](#answer-body) | Full text of a single answer |

---

## Common: Endpoint and Headers

All queries share the same endpoint pattern and required headers.

### Endpoint

```
POST https://www.quora.com/graphql/gql_para_POST?q=<QueryName>
```

### Headers

| Header | Value | Notes |
|--------|-------|-------|
| `Content-Type` | `application/json` | Required |
| `Quora-Formkey` | `<formkey>` | CSRF token; found in page cookies / JS vars |
| `Quora-Window-Id` | `react_<random>` | Browser session ID; from page JS |
| `Quora-Revision` | `<sha1 hash>` | JS bundle revision hash |
| `Quora-Broadcast-Id` | `main-w-chan...<windowId>-<token>` | Real-time channel ID |
| `Quora-Page-Creation-Time` | `<microseconds>` | Unix timestamp in microseconds |
| `Quora-Turnstile-Token` | `<token>` | Cloudflare Turnstile challenge token |
| `Quora-Canary-Revision` | `false` | Canary deployment flag |

All header values must be captured from an authenticated browser session by
intercepting a real page request (see [HOW_IT_WORKS.md](HOW_IT_WORKS.md)).

---

## Questions List

`UserProfileQuestionsList_Questions_Query`

### Endpoint

```
POST https://www.quora.com/graphql/gql_para_POST?q=UserProfileQuestionsList_Questions_Query
```

---

### Request Body

```json
{
  "queryName": "UserProfileQuestionsList_Questions_Query",
  "variables": {
    "uid": 229089,
    "first": 100,
    "after": null
  },
  "extensions": {
    "hash": "3af90ad8f3a28fee7837565ab4e334ad41ab03b1ae32c773cc233d7e4099d7aa"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `queryName` | string | Must be `UserProfileQuestionsList_Questions_Query` |
| `variables.uid` | integer | Quora user ID (numeric, not username) |
| `variables.first` | integer | Requested page size. Quora caps returns at 20 items regardless |
| `variables.after` | string\|null | Pagination cursor. `null` for first page; use `pageInfo.endCursor` for subsequent pages |
| `extensions.hash` | string | Relay persisted query hash; identifies the query on the server |

**Finding a user's `uid`:** Load any page in the user's profile and search the
HTML source for `"uid":` or intercept a GraphQL request — the uid appears in
the request body.

---

## Response

```json
{
  "data": {
    "user": {
      "uid": 229089,
      "recentPublicQuestionsConnection": {
        "pageInfo": {
          "hasNextPage": true,
          "endCursor": "19"
        },
        "edges": [
          {
            "cursor": "0",
            "node": {
              "title": "{\"sections\":[{\"spans\":[{\"text\":\"Question text here\",\"modifiers\":{}}],...}]}",
              "url": "/Question-Slug-Here",
              "qid": 123456789,
              "id": "UXVlc3Rpb246MTIzNDU2Nzg5",
              "isDeleted": false,
              "followerCount": 5
            }
          }
        ]
      }
    }
  },
  "extensions": { ... }
}
```

### Key response fields

| Field | Description |
|-------|-------------|
| `data.user.recentPublicQuestionsConnection.pageInfo.hasNextPage` | `true` if more pages exist |
| `data.user.recentPublicQuestionsConnection.pageInfo.endCursor` | Pass as `after` in the next request |
| `edges[].node.title` | **JSON-encoded rich text** — must be parsed (see below) |
| `edges[].node.url` | Relative URL, e.g. `/Why-is-the-sky-blue` or `/unanswered/Why-is-the-sky-blue` |
| `edges[].node.qid` | Numeric question ID |

### Parsing the `title` field

The `title` value is a JSON string in Quora's rich-text format:

```json
{
  "sections": [
    {
      "spans": [
        { "text": "Why is the sky blue?", "modifiers": {} }
      ],
      "type": "plain",
      "indent": 0,
      "quoted": false,
      "is_rtl": false
    }
  ]
}
```

Extract plain text:

```javascript
function parseTitle(titleJson) {
  const parsed = JSON.parse(titleJson);
  return parsed.sections
    .map(s => s.spans.map(sp => sp.text || '').join(''))
    .join(' ')
    .trim();
}
```

---

## Pagination

The cursor is a **zero-based numeric offset string**. With `first: 100`, the
server returns 20 items and sets `endCursor` to `"19"` (index of last item).
The next request uses `after: "19"` and gets items 20–39 with `endCursor: "39"`.

```
Page 1: after=null      → items 0–19,    endCursor="19"
Page 2: after="19"      → items 20–39,   endCursor="39"
Page 3: after="39"      → items 40–59,   endCursor="59"
...
Page N: after="<last>"  → final items,   hasNextPage=false
```

**Effective page size is 20** regardless of the `first` parameter value.

---

## Complete Pagination Loop (JavaScript)

Run inside the authenticated Quora tab (browser console or injected script):

```javascript
(async function extractAllQuestions(uid) {
  // Capture headers from a real request first (see HOW_IT_WORKS.md)
  const capturedHeaders = JSON.parse(localStorage.getItem('_gql_capture3'))[0].headers;
  const HASH = '3af90ad8f3a28fee7837565ab4e334ad41ab03b1ae32c773cc233d7e4099d7aa';

  function parseTitle(titleJson) {
    try {
      const parsed = JSON.parse(titleJson);
      return parsed.sections.map(s => s.spans.map(sp => sp.text || '').join('')).join(' ').trim();
    } catch(e) {
      return String(titleJson).trim();
    }
  }

  const results = [];
  let cursor = null;
  let hasNextPage = true;

  while (hasNextPage) {
    const resp = await fetch('/graphql/gql_para_POST?q=UserProfileQuestionsList_Questions_Query', {
      method: 'POST',
      credentials: 'include',
      headers: capturedHeaders,
      body: JSON.stringify({
        queryName: 'UserProfileQuestionsList_Questions_Query',
        variables: { uid, first: 100, after: cursor },
        extensions: { hash: HASH }
      })
    });

    const data = await resp.json();
    const conn = data.data.user.recentPublicQuestionsConnection;

    for (const edge of conn.edges) {
      results.push({
        title: parseTitle(edge.node.title),
        url: 'https://www.quora.com' + edge.node.url
      });
    }

    hasNextPage = conn.pageInfo.hasNextPage;
    cursor = conn.pageInfo.endCursor;

    await new Promise(r => setTimeout(r, 150)); // avoid rate limiting
  }

  return results;
})(229089);
```

---

## Observed Limits

| Parameter | Value |
|-----------|-------|
| Max items returned per call | 20 (server-enforced) |
| Max questions accessible via infinite scroll | ~6,851 (UI limit) |
| Max questions accessible via API | 14,691+ (tested) |
| Rate limiting onset | ~page 400+ (responses slow from ~150ms to ~3s) |

### Key Constants (as of 2026-05-22)

| Constant | Value |
|----------|-------|
| Query name | `UserProfileQuestionsList_Questions_Query` |
| Hash | `3af90ad8f3a28fee7837565ab4e334ad41ab03b1ae32c773cc233d7e4099d7aa` |
| uid (Miguel Paraz) | `229089` |
| Connection field | `data.user.recentPublicQuestionsConnection` |

---

## Authentication Requirements

- Must be called from an **authenticated browser session** (logged in to Quora)
- All `Quora-*` headers must match the active session
- The `Quora-Turnstile-Token` is a Cloudflare challenge token generated per session
- Cookies are sent automatically via `credentials: 'include'`

---

## How Headers Were Captured

Quora uses a **Service Worker** (`https://www.quora.com/sw.js`) that intercepts
all fetch calls before page-level JavaScript can see them. To capture real
request headers:

1. Unregister the service worker: `navigator.serviceWorker.getRegistrations().then(regs => regs.forEach(r => r.unregister()))`
2. Inject a `fetch` interceptor via `<script>` tag (runs in main world, not extension isolated world)
3. Trigger a scroll to cause the page to make a real request
4. Read captured headers from `localStorage`

See [HOW_IT_WORKS.md](HOW_IT_WORKS.md) for the full architecture.

---

## Answers List

`UserProfileAnswersMostRecent_RecentAnswers_Query`

Returns answers by a user in reverse-chronological order. Same pagination
mechanism as the questions query.

### Endpoint

```
POST https://www.quora.com/graphql/gql_para_POST?q=UserProfileAnswersMostRecent_RecentAnswers_Query
```

### Request Body

```json
{
  "queryName": "UserProfileAnswersMostRecent_RecentAnswers_Query",
  "variables": {
    "uid": 229089,
    "first": 100,
    "after": null,
    "answerFilterTid": null
  },
  "extensions": {
    "hash": "387718c70387d11d611f1aef67066ee1b645732539a4a48f593a2458a6bd11ae"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `variables.uid` | integer | Quora user ID |
| `variables.first` | integer | Requested page size; server caps at 20 |
| `variables.after` | string\|null | Pagination cursor (`null` for first page) |
| `variables.answerFilterTid` | null | Topic filter; pass `null` for all answers |

### Response

```json
{
  "data": {
    "user": {
      "recentPublicAndPinnedAnswersConnection": {
        "pageInfo": {
          "hasNextPage": true,
          "endCursor": "19"
        },
        "edges": [
          {
            "node": {
              "aid": 1477743908057477,
              "logUrl": "/Question-Slug/answer/Username/log",
              "question": {
                "qid": 225087876,
                "title": "{\"sections\":[...]}"
              }
            }
          }
        ]
      }
    }
  }
}
```

| Field | Description |
|-------|-------------|
| `edges[].node.aid` | Numeric answer ID — needed for `AnswerComponentBaseQuery` |
| `edges[].node.logUrl` | Relative URL with `/log` suffix; strip `/log` to get the answer URL |
| `edges[].node.question.title` | JSON rich text — parse with `parseTitle()` |
| `edges[].node.question.qid` | Numeric question ID |

**Constructing the answer URL:**
```javascript
const answerUrl = 'https://www.quora.com' + edge.node.logUrl.replace('/log', '');
```

### Observed Limits

| Parameter | Value |
|-----------|-------|
| Max items per call | 20 (server-enforced) |
| Max answers accessible via API | 13,295+ (tested) |
| Rate limiting onset | ~page 400+ |

### Key Constants (as of 2026-05-22)

| Constant | Value |
|----------|-------|
| Query name | `UserProfileAnswersMostRecent_RecentAnswers_Query` |
| Hash | `387718c70387d11d611f1aef67066ee1b645732539a4a48f593a2458a6bd11ae` |
| uid (Miguel Paraz) | `229089` |
| Connection field | `data.user.recentPublicAndPinnedAnswersConnection` |

---

## Answer Body

`AnswerComponentBaseQuery`

Fetches the full text and metadata of a single answer by its numeric `aid`.
One call per answer — not paginated.

### Endpoint

```
POST https://www.quora.com/graphql/gql_para_POST?q=AnswerComponentBaseQuery
```

### Request Body

```json
{
  "queryName": "AnswerComponentBaseQuery",
  "variables": {
    "aid": 1477743908057477,
    "showActionBarForLoggedOut": false,
    "skipFooter": false,
    "skipMetabar": false
  },
  "extensions": {
    "hash": "28e392de2fd885ba6962146a8bac8e6ec7c978af89c0d70e56695284efbe8a2b"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `variables.aid` | integer | Numeric answer ID (from answers list `edges[].node.aid`) |
| `variables.showActionBarForLoggedOut` | boolean | Pass `false` |
| `variables.skipFooter` | boolean | Pass `false` |
| `variables.skipMetabar` | boolean | Pass `false` |

All three boolean flags default to `false` and must be included; omitting them
causes a server error.

### Response

```json
{
  "data": {
    "answer": {
      "aid": 1477743908057477,
      "url": "/Question-Slug/answer/Username",
      "logUrl": "/Question-Slug/answer/Username/log",
      "isDeleted": false,
      "contentType": "answer",
      "isShortContent": false,
      "content": "{\"sections\":[{\"spans\":[{\"text\":\"Answer text here...\",\"modifiers\":{}}],\"type\":\"plain\",...}]}",
      "question": {
        "qid": 225087876,
        "title": "{\"sections\":[...]}"
      },
      "author": {
        "nick": "Miguel-Paraz"
      }
    }
  }
}
```

| Field | Description |
|-------|-------------|
| `data.answer.aid` | Numeric answer ID |
| `data.answer.url` | Relative answer URL (prepend `https://www.quora.com`) |
| `data.answer.content` | **JSON rich text** — full answer body, same format as `title`; parse with `parseTitle()` |
| `data.answer.isDeleted` | `true` if answer was deleted |
| `data.answer.isShortContent` | `true` for very short answers |
| `data.answer.question.title` | JSON rich text question title |
| `data.answer.question.qid` | Numeric question ID |
| `data.answer.author.nick` | Author's Quora username |

### Parsing Answer Content

The `content` field uses Quora's **Qtext** rich-text format — the same format
used for question titles and answer bodies. See [Qtext Format](#qtext-format)
below for the full specification and a parser that preserves images, links, and
formatting.

### Finding an answer's `aid`

Collect `aid` values from a prior `UserProfileAnswersMostRecent_RecentAnswers_Query`
run — each `edges[].node.aid` is the input for this query.

### Key Constants (as of 2026-05-23)

| Constant | Value |
|----------|-------|
| Query name | `AnswerComponentBaseQuery` |
| Hash | `28e392de2fd885ba6962146a8bac8e6ec7c978af89c0d70e56695284efbe8a2b` |
| Webpack chunk | `-4-ans_frontend-relay-rspack-query-AnswerComponentBaseQuery-27-11da9f4a783a92ac.webpack` |
| Content field | `data.answer.content` (JSON rich text) |
| Rate of use | One call per answer — no pagination |

---

## Activity Log / Comments

`UserProfileEditsQuery`

Returns all activity-log operations for a user in reverse-chronological order —
new comments, comment edits, answer edits, and new answers. Used to extract
comments a user has posted.

### Endpoint

```
POST https://www.quora.com/graphql/gql_para_POST?q=UserProfileEditsQuery
```

### Request Body

```json
{
  "queryName": "UserProfileEditsQuery",
  "variables": {
    "uid": 117344100,
    "first": 10,
    "after": null
  },
  "extensions": {
    "hash": "62bd99f6e2fc47e22ba048132f63cc0395a257041590981640afbf109829f4cc"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `variables.uid` | integer | Quora user ID |
| `variables.first` | integer | Requested page size; server caps at **10** |
| `variables.after` | string\|null | Pagination cursor; `null` for first page |

### Response

```json
{
  "data": {
    "user": {
      "logConnection": {
        "pageInfo": { "hasNextPage": true, "endCursor": "9" },
        "edges": [
          { "node": { "__typename": "AddAnswerCommentOperation", ... } }
        ]
      }
    }
  }
}
```

### Operation types

| `__typename` | Meaning |
|---|---|
| `AddAnswerCommentOperation` | User posted a new comment on an answer |
| `EditAnswerCommentOperation` | User edited an existing comment |
| `EditAnswerContentOperation` | User edited an answer body |
| `AttachAnswerOperation` | User created a new answer |

### `AddAnswerCommentOperation` fields

| Field | Type | Description |
|-------|------|-------------|
| `newContent` | string | Comment text as Qtext JSON — parse with `parseRich()` |
| `comment.url` | string | Relative URL: `/Question-Slug/answer/Username` |
| `time` | integer | Unix timestamp in **microseconds** |
| `opid` | integer | Numeric operation ID |

`EditAnswerCommentOperation` has the same fields plus `oldContent` (prior text as Qtext JSON).

**Constructing the comment URL:**
```javascript
const commentUrl = 'https://www.quora.com' + node.comment.url;
```

**Deriving a question title from the URL:**
```javascript
const slug = node.comment.url.split('/')[1] || '';
const questionTitle = slug.replace(/-/g, ' ');
```

**Converting the timestamp:**
```javascript
const date = new Date(node.time / 1000).toISOString().substring(0, 10);
```

### Observed Limits

| Parameter | Value |
|-----------|-------|
| Max items per call | 10 (server-enforced) |
| Total log entries (Alan Kay) | ~997 |
| `AddAnswerCommentOperation` entries (Alan Kay) | 584 |
| Estimated extraction time | ~15 seconds for full log |

### Key Constants (as of 2026-05-30)

| Constant | Value |
|----------|-------|
| Query name | `UserProfileEditsQuery` |
| Hash | `62bd99f6e2fc47e22ba048132f63cc0395a257041590981640afbf109829f4cc` |
| uid (Alan Kay) | `117344100` |
| Connection field | `data.user.logConnection` |

### Hard limit: activity log history

The activity log is **server-capped at approximately 997 entries** (confirmed for Alan Kay:
`hasNextPage: false` at cursor 996). This is not a client-side pagination limit — the server
simply does not expose older log entries. For Alan Kay, the oldest reachable comment is
**2023-11-16**, even though his answers date to 2016.

---

## Comment History: Known Limitations

Exhaustive investigation (2026-05-30) found **no way to retrieve comments older than the
activity log window** via Quora's client-accessible APIs. All approaches were tested:

### `CommentableCommentAreaLoaderInnerQuery`

This is the query Quora's JavaScript uses to load comments on answer pages.

```
POST https://www.quora.com/graphql/gql_para_POST?q=CommentableCommentAreaLoaderInnerQuery
```

**Variables confirmed from live capture:**

```json
{ "aid": 136609459636 }
```

(Note: `aid` here is the answer's internal numeric ID, which differs from the `aid` returned
by `UserProfileAnswersMostRecent_RecentAnswers_Query`. The format here is larger — appears
to be a different ID namespace.)

**Hash (as of 2026-05-30):**
```
e53caf9d1f42cdc72e45cdd642a8c80c61274bce64f68d446008d7c1e40c882b
```

**Result: consistently returns `{"errors":[{"message":"Server Error"}],"data":null}`**

This failure occurs even when the query is issued by the page's own JavaScript (captured
via fetch interceptor). Quora's service worker likely adds signed session headers that are
not reproducible outside the SW context. Comments therefore never render in the browser
when the service worker has been unregistered.

### SSR HTML

Answer page HTML fetched via `fetch(..., {credentials:'include'})` with navigation-style
`Accept` headers contains **no comment data**. Comments are not server-side rendered.

### Profile comments page

`https://www.quora.com/profile/<user>/comments` — **does not exist** (404). Quora profile
pages expose tabs for Answers, Questions, and Post only — no Comments tab.

### Summary

| Approach | Outcome |
|---|---|
| `UserProfileEditsQuery` | Works; hard-capped at ~997 entries (~latest 2-3 months of activity) |
| `CommentableCommentAreaLoaderInnerQuery` | Server Error — inaccessible outside SW context |
| SSR HTML scraping | No comment data in server response |
| Per-page DOM scraping (navigated) | Comments never render (same API failure) |
| `/profile/<user>/comments` | 404 — doesn't exist |

**Conclusion:** Only comments within the most recent ~997 activity log entries are
accessible. Older comments cannot be retrieved via any currently-known client-side method.

---

## Qtext Format

Quora's internal rich-text format used for question titles, answer bodies, and
other text fields. The value is a **JSON string** (not a nested object) that
must be parsed with `JSON.parse()`.

### Top-level structure

```json
{
  "sections": [
    {
      "type": "plain",
      "spans": [{ "text": "Hello world", "modifiers": {} }],
      "indent": 0,
      "quoted": false,
      "is_rtl": false
    }
  ]
}
```

### Section types

| `section.type` | Meaning | Render as |
|---|---|---|
| `"plain"` | Normal paragraph | Process spans inline |
| `"image"` | Standalone block image | `![](span.modifiers.image)` |
| `"horizontal-rule"` | Horizontal divider | `---` |
| `"hyperlink_embed"` | Embedded Quora link card | `> [title](url)` from `span.modifiers.embed` |
| `"yt-embed"` | YouTube embed | `> [YouTube video](url)` |
| `"ordered-list"` | Numbered list item | `1. text` |
| `"unordered-list"` | Bullet list item | `- text` |
| `"code"` | Code block | `` `text` `` |

For `"image"` sections, the image URL is `spans[0].modifiers.image` (a string).
The `spans[0].modifiers.master_url` field holds the same URL as a fallback.

**Image section example** (from aid 220300947):
```json
{
  "type": "image",
  "spans": [{
    "text": "",
    "modifiers": {
      "image": "https://qph.cf2.quoracdn.net/main-qimg-cc35dd482369cf6b4b7949e1fb50afe9-pjlq",
      "master_url": "https://qph.cf2.quoracdn.net/main-qimg-cc35dd482369cf6b4b7949e1fb50afe9-pjlq",
      "height": 310,
      "width": 222,
      "is_deleted": false
    }
  }]
}
```

### Span modifiers

Each span's `modifiers` object may contain zero or more of:

| Modifier key | Value type | Render as |
|---|---|---|
| `bold` | `true` | `**text**` |
| `italic` | `true` | `*text*` |
| `link` | `{ type, qid, url }` | `[text](url)` — use `.url` field |
| `image` | `"https://..."` | `![](url)` — string, not object |
| `embed` | `{ url, title, snippet, image_url }` | `[title](url)` |

**Link modifier example** (from aid 1477743867426570):
```json
{ "link": { "type": "question", "qid": 35785695, "url": "https://www.quora.com/..." } }
```

The `section.quoted` flag wraps the rendered line in `> ` blockquote prefix.

### Full rich-text parser (JavaScript, as of 2026-05-30)

Renders to Markdown preserving images, bold/italic, hyperlinks, and embeds.
Verified against 717 Alan Kay answers (222 image blocks, 1,210 hyperlinks,
405 bold spans, 91 embedded link cards).

```javascript
function parseRich(t) {
  try {
    const p = JSON.parse(t);
    return p.sections.map(s => {
      if (s.type === 'horizontal-rule') return '---';
      if (s.type === 'image') {
        const span = s.spans?.[0];
        const url = span?.modifiers?.image || span?.modifiers?.master_url;
        return url ? `![](${url})` : '';
      }
      if (s.type === 'hyperlink_embed') {
        const span = s.spans?.[0];
        const embed = span?.modifiers?.embed;
        if (!embed?.url) return '';
        return `> [${embed.title || embed.url}](${embed.url})`;
      }
      if (s.type === 'yt-embed') {
        const span = s.spans?.[0];
        const mods = span?.modifiers || {};
        const url = mods.yt?.url || mods.url || mods.embed?.url || '';
        return url ? `> [YouTube video](${url})` : '';
      }
      // plain, ordered-list, unordered-list, code, and other inline types
      const lineText = s.spans.map(sp => {
        const mods = sp.modifiers || {};
        let text = sp.text || '';
        if (mods.image) return `![](${mods.image})`;
        if (mods.embed?.url) return `[${mods.embed.title || mods.embed.url}](${mods.embed.url})`;
        if (mods.link?.url) text = `[${text}](${mods.link.url})`;
        if (mods.bold && mods.italic) text = `***${text}***`;
        else if (mods.bold) text = `**${text}**`;
        else if (mods.italic) text = `*${text}*`;
        return text;
      }).join('');
      if (s.quoted) return lineText.split('\n').map(l => `> ${l}`).join('\n');
      return lineText;
    }).join('\n').trim();
  } catch(e) { return String(t || '').trim(); }
}
```

### Plain-text-only parser

When formatting is not needed (e.g., question titles):

```javascript
function parseTitle(t) {
  try {
    const p = JSON.parse(t);
    return p.sections.map(s => s.spans.map(sp => sp.text || '').join('')).join(' ').trim();
  } catch(e) { return String(t || '').trim(); }
}
```

Use `'\n'` instead of `' '` as the section joiner for answer bodies to preserve
paragraph breaks.

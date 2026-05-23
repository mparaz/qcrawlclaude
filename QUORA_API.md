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

The `content` field uses the same JSON rich-text format as question titles:

```javascript
function parseAnswerText(contentJson) {
  try {
    const parsed = JSON.parse(contentJson);
    return parsed.sections
      .map(s => s.spans.map(sp => sp.text || '').join(''))
      .join('\n')
      .trim();
  } catch(e) {
    return String(contentJson).trim();
  }
}
```

Note: use `'\n'` as the section separator (not `' '`) to preserve paragraph
breaks in the answer body.

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

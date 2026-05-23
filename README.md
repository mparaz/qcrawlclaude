# qcrawlclaude

Extracts all questions and answers from a Quora user profile via Quora's
internal GraphQL API, using Claude Code with the Claude-in-Chrome browser
extension.

## What's in this repo

| File | Description |
|------|-------------|
| `miguel_paraz_quora_questions.md` | 14,691 questions asked by Miguel Paraz |
| `miguel_paraz_quora_answers.md` | 13,295 answers written by Miguel Paraz |
| `QUORA_API.md` | Reverse-engineered Quora GraphQL API documentation |
| `HOW_IT_WORKS.md` | Architecture and technique explanation |
| `RUNBOOK_QUESTIONS.md` | Step-by-step guide to re-run question extraction |
| `RUNBOOK_ANSWERS.md` | Step-by-step guide to re-run answer extraction |

## How it works

Quora does not have a public API. This project intercepts the internal GraphQL
API that Quora's own web frontend uses:

1. Claude navigates a logged-in Chrome tab to the user's profile page
2. Quora's Service Worker (which intercepts fetch calls) is unregistered
3. A fetch interceptor is injected into the page's main JavaScript world
4. A scroll event triggers the page to make a real GraphQL request, capturing
   the session headers (including the short-lived Cloudflare Turnstile token)
5. An extraction loop makes paginated GraphQL calls directly, storing results
   in `localStorage` in chunks to stay within the quota
6. A Python script reads `localStorage` via AppleScript and writes a Markdown file

See [HOW_IT_WORKS.md](HOW_IT_WORKS.md) for full details.

## Queries used

| Query | Data |
|-------|------|
| `UserProfileQuestionsList_Questions_Query` | All questions (paginated, 20/call) |
| `UserProfileAnswersMostRecent_RecentAnswers_Query` | All answers (paginated, 20/call) |
| `AnswerComponentBaseQuery` | Full text of a single answer |

See [QUORA_API.md](QUORA_API.md) for request/response structure, hashes, and
pagination details.

## Requirements

- macOS (AppleScript used to bridge browser ↔ Python)
- Google Chrome with "Allow JavaScript from Apple Events" enabled
  (View → Developer → Allow JavaScript from Apple Events)
- [Claude Code](https://claude.ai/code) CLI
- [Claude-in-Chrome](https://github.com/anthropics/claude-in-chrome) MCP extension
- A logged-in Quora session in Chrome

## Re-running

Follow [RUNBOOK_QUESTIONS.md](RUNBOOK_QUESTIONS.md) or
[RUNBOOK_ANSWERS.md](RUNBOOK_ANSWERS.md). The API hashes change with each
Quora frontend deployment — the runbooks explain how to discover the current hash.

## Disclaimer

This uses Quora's undocumented internal API. It may break at any time. Use
only for accessing your own data and in accordance with Quora's terms of service.

## License

MIT — see [LICENSE](LICENSE).

// mock-llm-server.mjs — a tiny, deterministic OpenAI-compatible SSE server for QA.
//
// Exists so the concurrent-conversations acceptance criteria that need a GENUINELY LIVE turn
// (E1/E2/E4 — the `working` indicator exists only in backend.ai.live_sessions's IN-PROCESS
// registry while a real turn executes; it has no file/IndexedDB backing, so it cannot be
// seeded the way `last_turn_status` can) can be proven WITHOUT calling a paid provider. The
// Anthropic account is over its usage limit until 2026-09-01 — this has zero network egress
// and zero cost; it never leaves 127.0.0.1.
//
// Implements just enough of `POST /v1/chat/completions` — the streaming Chat Completions wire
// format the `openai` Python SDK speaks (see backend/ai/models/openai_provider.py, the ONLY
// consumer: `base_url` + `api_key` are passed explicitly into the SDK's client constructor, so
// pointing ModelConfigStore's `openai_compatible` provider at this server is all it takes) — to
// drive a REAL turn through `backend.ai.turns.spawn` -> `assistant.run`'s tool loop:
//
//   - round 1 (no tool result in `messages` yet): a few words of "thinking" text, then a
//     `tool_calls` round calling the real, read-only `get_portfolio` tool with `input={}`
//     (safe on any book — no confirm gate, no side effects).
//   - round 2 (a `role: "tool"` message is back in `messages` — the tool already ran for
//     real): either a normal final answer (`finish_reason: "stop"`), or — when the ORIGINAL
//     prompt carries FAIL_MARKER — an HTTP 500 instead of a stream, so the `openai` SDK raises
//     and the turn ends in `backend.ai.turns._drive`'s `except Exception` branch exactly like a
//     real provider outage would.
//
// Every step is deliberately SLOW (word-by-word, second-scale delays) so the `working`
// indicator has time to be observed by a screenshot script across more than one
// GET /api/sessions/live poll (frontend/src/state/luciusChat.tsx's LIVE_POLL_MS = 4000).
//
// Any request that doesn't carry SCENARIO_MARKER — a background job sharing the same
// instance-default provider (suggestions, memory, price narration, ...) — gets a fast,
// minimal, harmless completion instead of the slow scripted scenario, so nothing else in the
// app hangs waiting on it or gets an unexpected tool call.

import http from 'node:http'

export const SCENARIO_MARKER = 'QA-LIVE-TURN-'
export const FAIL_MARKER = 'QA-LIVE-TURN-FAIL'

const WORD_DELAY_MS = 320
const ROUND1_PRE_DELAY_MS = 500
const ROUND2_PRE_DELAY_MS = 1200
const FAIL_PRE_DELAY_MS = 4000

const ROUND1_TEXT = 'Let me pull up your current portfolio to check that for you.'
const ROUND2_TEXT =
  'Based on your holdings, your portfolio looks diversified across equities, cash and a ' +
  'bit of crypto. Let me know if you want a deeper breakdown of any position.'

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function sseChunk(res, obj) {
  if (res.destroyed || res.writableEnded) return
  res.write(`data: ${JSON.stringify(obj)}\n\n`)
}

function baseChunk(id, created, model) {
  return { id, object: 'chat.completion.chunk', created, model }
}

function messageText(message) {
  const content = message?.content
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content.map((p) => (typeof p?.text === 'string' ? p.text : '')).join(' ')
  }
  return ''
}

async function streamWords(res, id, created, model, text) {
  const words = text.split(' ')
  for (let i = 0; i < words.length; i++) {
    if (res.destroyed || res.writableEnded) return
    const piece = words[i] + (i < words.length - 1 ? ' ' : '')
    sseChunk(res, {
      ...baseChunk(id, created, model),
      choices: [{ index: 0, delta: { content: piece }, finish_reason: null }],
    })
    await sleep(WORD_DELAY_MS)
  }
}

async function handleRound1(res, id, created, model) {
  sseChunk(res, {
    ...baseChunk(id, created, model),
    choices: [{ index: 0, delta: { role: 'assistant' }, finish_reason: null }],
  })
  await sleep(ROUND1_PRE_DELAY_MS)
  await streamWords(res, id, created, model, ROUND1_TEXT)
  sseChunk(res, {
    ...baseChunk(id, created, model),
    choices: [
      {
        index: 0,
        delta: {
          tool_calls: [
            {
              index: 0,
              id: 'call_qa_mock_1',
              type: 'function',
              function: { name: 'get_portfolio', arguments: '' },
            },
          ],
        },
        finish_reason: null,
      },
    ],
  })
  sseChunk(res, {
    ...baseChunk(id, created, model),
    choices: [
      { index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: '{}' } }] }, finish_reason: null },
    ],
  })
  sseChunk(res, {
    ...baseChunk(id, created, model),
    choices: [{ index: 0, delta: {}, finish_reason: 'tool_calls' }],
  })
  sseChunk(res, {
    ...baseChunk(id, created, model),
    choices: [],
    usage: { prompt_tokens: 400, completion_tokens: 20, total_tokens: 420 },
  })
  if (!res.writableEnded) {
    res.write('data: [DONE]\n\n')
    res.end()
  }
}

async function handleRound2Success(res, id, created, model) {
  await sleep(ROUND2_PRE_DELAY_MS)
  await streamWords(res, id, created, model, ROUND2_TEXT)
  sseChunk(res, {
    ...baseChunk(id, created, model),
    choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
  })
  sseChunk(res, {
    ...baseChunk(id, created, model),
    choices: [],
    usage: { prompt_tokens: 500, completion_tokens: 40, total_tokens: 540 },
  })
  if (!res.writableEnded) {
    res.write('data: [DONE]\n\n')
    res.end()
  }
}

async function handleRound2Fail(res) {
  // Deliberately delayed rather than instant: the whole point is a turn that has already
  // shown `working` (round 1 + a real tool execution already happened) before it fails, not
  // one that fails before any structure exists — see the module docstring.
  await sleep(FAIL_PRE_DELAY_MS)
  if (res.destroyed) return
  res.writeHead(500, { 'Content-Type': 'application/json' })
  res.end(
    JSON.stringify({
      error: {
        message: 'QA mock: deliberate failure for concurrent-conversations E2 error-path evidence',
        type: 'mock_error',
        code: 'qa_mock_fail',
      },
    }),
  )
}

async function handleBackground(res, id, created, model, stream) {
  // Not our scenario — a background task (suggestions/memory/price narration/...) sharing the
  // instance-default provider. Fast, minimal, harmless; never the slow scripted shape.
  if (!stream) {
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(
      JSON.stringify({
        id,
        object: 'chat.completion',
        created,
        model,
        choices: [{ index: 0, message: { role: 'assistant', content: 'ok' }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 10, completion_tokens: 1, total_tokens: 11 },
      }),
    )
    return
  }
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
  })
  sseChunk(res, {
    ...baseChunk(id, created, model),
    choices: [{ index: 0, delta: { role: 'assistant', content: 'ok' }, finish_reason: null }],
  })
  sseChunk(res, { ...baseChunk(id, created, model), choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] })
  res.write('data: [DONE]\n\n')
  res.end()
}

async function handleRequest(req, res) {
  if (req.method !== 'POST') {
    res.writeHead(404)
    res.end()
    return
  }
  let body = ''
  req.on('data', (chunk) => {
    body += chunk
  })
  req.on('end', () => {
    void (async () => {
      let parsed
      try {
        parsed = JSON.parse(body || '{}')
      } catch {
        res.writeHead(400, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ error: { message: 'QA mock: invalid JSON body' } }))
        return
      }
      const messages = Array.isArray(parsed.messages) ? parsed.messages : []
      const model = parsed.model || 'qa-mock-model'
      const stream = parsed.stream === true
      const id = `chatcmpl-qamock-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      const created = Math.floor(Date.now() / 1000)

      const allText = messages.map(messageText).join('\n')
      const isOurs = allText.includes(SCENARIO_MARKER)
      const isFail = allText.includes(FAIL_MARKER)
      const hasToolResult = messages.some((m) => m.role === 'tool')

      if (!isOurs) {
        await handleBackground(res, id, created, model, stream)
        return
      }
      if (!stream) {
        // The real chat path always requests stream:true; a non-streaming call carrying our
        // marker is unexpected — fail loudly rather than silently guessing at a shape nothing
        // in this scenario exercises.
        res.writeHead(500, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ error: { message: 'QA mock: expected a streaming request' } }))
        return
      }
      if (hasToolResult && isFail) {
        // Decide the status code BEFORE writing any header — a 500 cannot follow a 200.
        await handleRound2Fail(res)
        return
      }
      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      })
      if (!hasToolResult) {
        await handleRound1(res, id, created, model)
      } else {
        await handleRound2Success(res, id, created, model)
      }
    })().catch((e) => {
      // A stream write against a connection the client already tore down — best-effort only,
      // never let it crash the mock server process out from under a still-running scenario.
      console.error('[mock-llm] request handler error:', e.message)
      try {
        if (!res.headersSent) res.writeHead(500)
        res.end()
      } catch {
        /* connection already gone */
      }
    })
  })
}

/** Start the mock server on a free loopback port. Resolves `{ port, url, stop }` — `url` is
 *  the bare origin (`http://127.0.0.1:PORT`); the caller appends `/v1` for ModelConfigStore's
 *  `base_url` (matching every real openai_compatible catalog entry, e.g. `.../v1`). */
export function startMockLlmServer() {
  return new Promise((resolve, reject) => {
    const server = http.createServer(handleRequest)
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      const url = `http://127.0.0.1:${port}`
      const stop = () =>
        new Promise((res) => {
          server.close(() => res())
          // Do not let lingering keep-alive sockets (a client that disconnected mid-stream)
          // block shutdown — force-close after a short grace period.
          setTimeout(() => {
            server.closeAllConnections?.()
            res()
          }, 500)
        })
      resolve({ port, url, stop })
    })
  })
}

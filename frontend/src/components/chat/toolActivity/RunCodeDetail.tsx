// `run_code`'s own expanded view — moved verbatim out of `MessageBubble.tsx`, restyled onto
// the shared boxes.
//
// It exists because the generic Request/Result view renders the model-authored SCRIPT as an
// escaped JSON one-liner, which is unreadable exactly when you most want to read it. Here it
// is a real multi-line code block, and the result shows the data or the error rather than
// the SandboxResult bookkeeping around it.

export function RunCodeDetail({
  input,
  result,
}: {
  input?: Record<string, unknown>
  result?: string
}) {
  const inp = (input || {}) as { source?: string; input?: unknown; purpose?: string }
  const source = typeof inp.source === 'string' ? inp.source : ''
  const scriptInput = inp.input

  let parsed: Record<string, unknown> | null = null
  if (result !== undefined) {
    try {
      parsed = JSON.parse(result) as Record<string, unknown>
    } catch {
      parsed = null
    }
  }

  // Prefer the data, then stdout — the fields a person actually wants — over the envelope.
  let resultBody: string | null = null
  let resultIsError = false
  if (parsed) {
    if (parsed.error) {
      resultBody = String(parsed.error)
      resultIsError = true
    } else if (parsed.data != null) {
      resultBody = JSON.stringify(parsed.data, null, 2)
    } else if (typeof parsed.stdout === 'string' && parsed.stdout.trim()) {
      resultBody = parsed.stdout.trim()
    }
  } else if (result) {
    resultBody = result
  }
  const durationMs = parsed && typeof parsed.duration_ms === 'number' ? parsed.duration_ms : null

  return (
    <div className="msg-toolact__detail">
      {inp.purpose && (
        <div className="msg-toolact__box">
          <div className="msg-toolact__box-head">
            <span className="msg-toolact__box-title">Purpose</span>
          </div>
          <div className="msg-toolact__purpose">{inp.purpose}</div>
        </div>
      )}
      {source && (
        <div className="msg-toolact__box">
          <div className="msg-toolact__box-head">
            <span className="msg-toolact__box-title">Script</span>
            <span className="msg-toolact__box-meta">python</span>
          </div>
          <pre className="msg-toolact__code">{source}</pre>
        </div>
      )}
      {scriptInput != null && JSON.stringify(scriptInput) !== '{}' && (
        <div className="msg-toolact__box">
          <div className="msg-toolact__box-head">
            <span className="msg-toolact__box-title">Input</span>
          </div>
          <pre className="msg-toolact__code">{JSON.stringify(scriptInput, null, 2)}</pre>
        </div>
      )}
      {resultBody !== null && (
        <div className="msg-toolact__box">
          <div className="msg-toolact__box-head">
            <span className="msg-toolact__box-title">Result</span>
            {durationMs != null && <span className="msg-toolact__box-meta">{durationMs}ms</span>}
          </div>
          <pre className={`msg-toolact__code${resultIsError ? ' msg-toolact__code--error' : ''}`}>
            {resultBody}
          </pre>
        </div>
      )}
    </div>
  )
}

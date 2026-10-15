"""Tool-surface A/B harness — which tool surface does the model perform better against?

Compares three surfaces over the SAME underlying service choke points, so the only
variable is the shape of the surface the model sees:

  A  many named tools, core schemas inline, rare tools via find_tools  (today's design)
  B  few fat tools with an `action` discriminator, everything always loaded
  C  a thin apply(op, items) dispatcher whose per-op shapes are fetched via describe()

Plus ``A-real``, a FIDELITY CONTROL that runs the actual production registry so the
A-shim can be checked against the real thing rather than assumed faithful.

Scoring is outcome-based: after each run the resulting DATA is diffed against a
snapshot, so a task passes only if the intended row landed AND nothing unintended did.

Entry point: ``python3 -m backend.evals.tool_surface --mock`` (see ``__main__.py``).
"""

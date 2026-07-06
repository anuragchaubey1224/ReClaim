"""AI layer (L4) — the grounded co-pilot on top of the deterministic engine.

Everything here is *optional*: the engine (scan / classify / plan / apply / undo) works
fully with the AI turned off (invariant I7). This package adds a natural-language front end
that is architecturally incapable of unsafe action (docs/05):

  * `tools`       — a small, **read-only** fact toolset over the engine. The agent can only
                    *query* classifier/planner facts; there is no delete or shell tool (I5).
  * `providers`   — a provider-neutral tool-calling contract with a BYOK Claude backend and a
                    local Ollama fallback. The engine depends on the *contract*, not a vendor.

The agent loop (Phase 2b) and preference memory / explanations (Phase 2c) build on top of
this layer. Whatever the model proposes still funnels through the one Safety Gate (L3).
"""

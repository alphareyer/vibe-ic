# F2 integration increment

F1 registers `execution_adapters_frontend.STEP_IDS` and calls `prepare` with the
seed `StepRequest`; it uses the unchanged Controller run/adopt API, followed by
the returned `consume(project, context, controller, run, adopted)` callable.
All three operations run in the same live parent interpreter. F1 owns the
protocol, registrar and phase hooks; this branch does not alter them.

`FrontendContext.binding()` rechecks complete lexical input populations,
including missing roots, additions, deletion, aliases and resolved bytes, and
the exact source and lease identities. Inputs are frozen at preparation; the
step's downstream outputs are excluded, while consumed upstream reports remain
inputs. F1 supplies explicit per-step roots; there is no nineteen-step global
fallback. The delegated worker routes all nineteen assigned rows to their
existing producers and canonical gate AST. Its consumer publishes only exact
issued canonical output bytes through the existing held-directory CAS and
rollback helpers. See `execution_frontend_f2_interface.md` for concrete typed
parameters, native dependencies and returned proof fields.

This is source qualification, not an IC run, native measurement or acceptance.

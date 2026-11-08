# Test review record

The JSON ledgers record a review of each baseline and current test function,
including removed tests and materially distinct parameter cases. This is a
snapshot of this modernization review, not a substitute for reviewing future
tests.

The baseline contains 388 test functions. The reviewed suite contains 371
functions and collects 584 cases. An independent inventory compared Python AST
function names (including async functions) in the baseline and working tree
against the ledgers: no functions were missing. Some entries describe individual
parameter cases; `wheel_knowledge_smoke.py::main` describes an auxiliary package
smoke program. Entry counts therefore differ from pytest case counts.

Each entry states its evidence boundary. Input validation, request dispatch,
generated source contracts, executed API doubles, protocol simulations, and real
MCP client/server exchanges prove different things. None of the offline tests
prove Fusion's geometric behavior. Source-contract tests retained here check
specific generated requests or escaping, and are labeled accordingly.

Obsolete packaging comparisons and dependency on an unbundled local corpus were
replaced with synthetic corpus tests and a built-wheel installation probe.
Comment-only checks and misleading mocked API assumptions were removed or
replaced. The ledger preserves the reasons, including tests that previously
protected incorrect document, mesh, joint, hole, or camera behavior.

The opt-in `scripts/live_regression.py` uses a running Fusion instance and creates
its own disposable document. It checks independently measured dimensions,
volumes, instances, collisions, file contents, and failure recovery. Its report
distinguishes passed checks from capabilities blocked by the installed build.
It neither saves nor changes user documents. `--keep-open` leaves its fixture
available for inspection; otherwise it closes the fixture and restores the
previous active document.

# Daily report implementation and acceptance

The existing app, market analysis, Matplotlib report and ServerChan transport are retained and extended. The supplied specification is the approved design; implementation proceeds directly as requested.

- [x] Data adapters, per-symbol fallback, validated adjusted OHLCV and bounded concurrency/cache.
- [x] Complete US exchange security master and staged liquidity / trend / structure scan.
- [x] Completed NYSE sessions and weeks, strict Stage transitions, RS, support/resistance and observable RR.
- [x] Fixed TEM / RXRX / ENPH cards and independently recalculated daily candidates.
- [x] Publish PNG, verify anonymous bytes, send, then persist session; fail closed on uncertain state.
- [x] Unit/regression tests plus real market fetch, scan and visual PNG inspection.
- [x] GitHub repository, encrypted Actions Secret, manual workflow and duplicate-session run.

Ownership: data provider/universe, analysis/scanner, report renderer are separate modules; app, state, delivery, deployment and final integration are coordinated centrally.

Tests: `.venv/Scripts/python.exe -m pytest -q` locally and `python -m pytest -q` on Linux CI. Live acceptance: `python -m src.app prepare`; delivery: `python -m src.app run-github` on Actions. Scanner/provider diagnostics are saved with the report and logs.

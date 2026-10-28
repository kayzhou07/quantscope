# Verification record

Date: 2026-10-29. Scope: this repository reorganization and its offline unified interfaces.

| Check | Result |
| --- | --- |
| Unit, regression, boundary and local HTTP tests | 23 tests pass |
| QuantLibra comparison to preserved original | 100 seeded synthetic price sets; momentum values, ranks and target agree |
| Source static parsing | All delivered Python sources parse; a pre-existing report text quoting error was repaired |
| Package build | Wheel built successfully with no third-party core runtime dependency |
| Clean install | Wheel installed into a separate virtual environment; frozen regime package data loaded |
| Offline examples | Four model inputs, portfolio combination and SuperMind target export run successfully |
| Live local HTTP | Signal request returns 200; malformed JSON returns 400; loopback binding |
| Release scan | No matched credential, personal-home, phone/identity pattern or runtime-state file in deliverable text |
| English business plan | 15 A4 pages, all sections rendered; image/caption layout and budget reviewed |
| Chinese business plan | Latest revised 15-page edition included; model predecessor captions retained |

The source scan distinguishes SHA256 digests and numeric fractions from personal identifiers. It covers deliverable text, not hidden metadata or all pixels in historical binary media. Historical binary evidence is kept in a separate local archive rather than submitted to Git.

## Reproduce

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
python scripts/release_check.py
python -m quantscope signal examples/quantvela.json
python -m quantscope signal examples/quantorion.json
python -m quantscope signal examples/quantlibra.json
python -m quantscope signal examples/quantpyxis.json
python -m quantscope portfolio examples/portfolio.json
python -m quantscope export examples/portfolio-signal.json
```

## Explicit limits

No historical full-data backtest was rerun. No remote data/API key was used for tests. No actual order was submitted, no company investment performance was established and no three-model vendor integration was certified. Original platform scripts require their supported platform environments; original local engines require their own data/configuration. CI verifies the offline core and release boundaries rather than executing archived trading callbacks.

Python 3.12 was tested locally; the GitHub workflow also defines 3.11 and 3.13 jobs. Remote CI status must be read from actual GitHub runs, not inferred from this table. No precise historical dependency lock was supplied, and none was invented.

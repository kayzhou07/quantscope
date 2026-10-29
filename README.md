# QuantScope · 量见

A unified research workspace for three quantitative models and one financial technology research project.

[中文说明](README.zh-CN.md) · [Architecture](docs/architecture.md) · [Business plan (English)](docs/business-plan/business-plan.en.md) · [English PDF](docs/business-plan/QuantScope-Business-Plan-EN.pdf) · [中文计划书 PDF](docs/business-plan/QuantScope-Business-Plan-ZH.pdf)

| ID | Name | Responsibility | Current scope |
| --- | --- | --- | --- |
| `quantvela` | 量寰 QuantVela | Global allocation and US ETF research | Newly implemented rule functions from research notes; no original full backtest engine supplied |
| `quantorion` | 量曜 QuantOrion | A-share market structure and consecutive-limit-up / volume-contraction screening | Offline signals plus preserved Web prototype; formerly AlphaBoard |
| `quantlibra` | 量衡 QuantLibra | ETF dual momentum, allocation and static backtest checks | Offline signals, preserved Web prototype and JoinQuant research version; formerly QuantBalance |
| `quantpyxis` | 量枢 QuantPyxis | Cross-sectional A-share momentum and regime risk budgets | Frozen-A target snapshot, original local engines and SuperMind historical backtest sources |

## Contributors and AI acknowledgements

| Name | Role |
| --- | --- |
| [@kayzhou07](https://github.com/kayzhou07) | Project owner and maintainer |
| [@Tang666-0427](https://github.com/Tang666-0427) | Contributor |
| [OpenAI — Codex / ChatGPT](https://github.com/openai) | AI-assisted code organization, documentation and development support |
| [DeepSeek](https://github.com/deepseek-ai) | AI tooling acknowledged for the project's research and report workflows |

AI acknowledgements identify tools used; they do not imply that the official organizations contributed commits, maintain this project or endorse it. [Contribution details](CONTRIBUTORS.md).

## Quick start

Python 3.11+; the unified core has **no third-party runtime dependencies**.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
quantscope models
quantscope signal examples/quantlibra.json
quantscope portfolio examples/portfolio.json --output runtime-target.json
quantscope export examples/portfolio-signal.json
python -m unittest discover -s tests -v
```

Without installation: `PYTHONPATH=src python3 -m quantscope models` (macOS/Linux).
All bundled examples are **synthetic**, explicitly dated fixtures, not current recommendations, performance records or live positions.

## Repository layout

```text
src/quantscope/
  data/                       # Strict input and date validation
  models/quantvela/           # Research rules; daily and monthly branches
  models/quantorion/          # Two-day limit / volume screen
  models/quantlibra/          # Dual momentum and cooldown
  models/quantpyxis/          # Frozen-A scoring and risk budget
  portfolio/                 # Explicit model budgets and concentration checks
  adapters/                  # Offline SuperMind target export
  interfaces/                # CLI and local HTTP API
prototypes/                  # Preserved, sanitized earlier Web projects
research/                    # Version-separated research and platform sources
examples/                    # Synthetic input/output and portfolio fixtures
apps/                        # Web/mobile integration contracts
config/                      # Model and portfolio policy documentation
scripts/                     # Reproducibility and release checks
tests/                      # Offline regression and boundary tests
docs/                       # Architecture, migration, business plans, operations
```

## Shared Web / mobile API

```bash
quantscope serve --port 8765
curl http://127.0.0.1:8765/api/v1/models
curl -H 'Content-Type: application/json' --data-binary @examples/quantlibra.json http://127.0.0.1:8765/api/v1/signals
```

The API binds to loopback only. It has no user authentication and is intended for local development. A production Web service or native mobile application is not part of this release; see [terminal contracts](apps/README.md).

## Research, execution and version boundaries

- Signals contain a completed `signal_date` and a strictly later `execute_after`. The caller must provide an exchange calendar; the core does not invent a next trading session.
- Empty successful targets are distinct from invalid or expired inputs. Failed signals cannot enter the portfolio or platform export.
- Vela is excluded from the three-model operating portfolio. Its monthly and daily branches must not be combined into a claimed historical track record.
- Libra's local CLI uses **26** price observations while its original JoinQuant version uses **25**. Both variants are named explicitly; the default matches the local source.
- Pyxis's frozen regimes end on **2026-10-19**. Later dates fail as `expired` unless an explicitly dated external regime is supplied. That option is an input contract, not a validated new regime classifier.
- The SuperMind adapter exports an internal target snapshot only. It does not claim to implement a vendor SDK or submit orders. [Migration status](docs/migration.md) states which integrations remain pending.
- Original historical code and source notes are retained separately; no full historical backtest or commercial platform integration is certified by this reorganization.

## Historical materials

Every recovered source file is inventoried in [source_inventory.json](docs/migration/source_inventory.json). Large market data, media and detailed evidence are delivered as a separate local archive, intentionally outside Git; see [materials guide](docs/materials.md). Text normalization and exclusions are recorded. Archived reports retain their historical claims and known weaknesses; they are not new performance endorsements.

## Licensing and publication

All rights reserved; no open-source license has been granted. Secrets and personal application data must not be committed. Use `.env.example` only as a template. Default publication should be a **private** repository until code and dataset redistribution rights are confirmed. This is a repository visibility choice, not a claim of financial authorization.

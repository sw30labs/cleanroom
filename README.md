```
  ____  _      _____      _     _   _  ____     ___    ___   __  __
 / ___|| |    | ____|    / \   | \ | ||  _ \   / _ \  / _ \ |  \/  |
| |    | |    |  _|     / _ \  |  \| || |_) | | | | || | | || |\/| |
| |___ | |___ | |___   / ___ \ | |\  ||  _ <  | |_| || |_| || |  | |
 \____||_____||_____| /_/   \_\|_| \_||_| \_\  \___/  \___/ |_|  |_|
```

# Cleanroom

**An agentic file-hygiene pipeline — Analyze → Recommend → Remediate → QA for any single file.**

Cleanroom takes one input file (any format) and runs it through a four-agent
[LangGraph](https://www.langchain.com/langgraph) workflow that classifies it,
detects provenance marks, proposes a remediation plan, applies a cleaned
sibling, and QA-verifies the result with a bounded judge-and-revise loop.

> **What it is not:** a magic eraser. Deterministic removals are *verifiable*
> (invisible-Unicode counts, metadata actions). Statistical text-watermark
> reduction via rewrite, and the QA judge verdict, are *best-effort* — no tool
> can certify that a vendor detector will fail. Cleanroom is built for hygiene
> and privacy on content you own, and it says so honestly.

## The workflow

| Phase | Agent | Purpose | Output |
|---|---|---|---|
| **1. Analyze** | classifier + reader | Classify the file, extract readable content, run deterministic mark detection, optional semantic read | File profile + mark report |
| **2. Recommend** | planner | Build a ranked remediation plan (lossless deterministic actions first, escalate only if needed) | Action plan |
| **3. Remediate** | editor | Apply the plan via safe atomic writes, writing a `*.cleaned.*` sibling — never mutating your original in place | Cleaned file + action log |
| **4. QA** | judge | Deterministic checks + a bounded judge-and-revise loop scoring residual marks and fidelity | PASS / REVISE / FAIL verdict |

LangGraph topology:

```text
start → analyze → recommend → remediate → qa → end
```

Deterministic phases run without any LLM. The semantic, rewrite, and judge
steps are attempted and **degrade gracefully** when the endpoint is offline —
a `safe` run produces a working cleaned file with no model at all.

## What it removes (and the honest limits)

| Channel | What we do | Verifiable? |
|---|---|---|
| Invisible Unicode (ZWSP, bidi, word joiner, tags, exotic spaces, format chars) | Deterministic strip (exotic spaces → ASCII space) | ✅ counted |
| Visible Latin typography (em dash, curly quotes, ñ, other diacritics) | Deterministic ASCII fold (`—` → `--`, `ñ` → `n`) | ✅ counted |
| AI YAML frontmatter keys (`ai`, `model`, `generator`, …) | Deterministic strip | ✅ counted |
| Container metadata (PNG/JPEG C2PA/EXIF/XMP, DOCX/ODT props, PDF XMP) | Flagged in Analyze; strip pending a format-aware tool (exiftool/c2patool) or LLM-rewrite repackage | ⚠️ partial |
| Statistical token-sampling text watermarks | LLM rewrite (aggressive strength) | ❌ best-effort |
| C2PA **soft binding**, pixel/audio/video watermarks | Out of scope for removal | ❌ |
| Training backdoors / trigger phrases | Out of scope | ❌ |

## Quick start

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev,pdf]'
python -m pytest          # or: make test
```

### CLI

```bash
# Inspect a file (no LLM needed)
python -m cleanroom.cli inspect notes.md

# Run the full 4-phase workflow
python -m cleanroom.cli run notes.md --out generated/

# Aggressive strength (LLM rewrite) — requires a reachable endpoint
python -m cleanroom.cli run draft.md --strength aggressive

# Preview without writing
python -m cleanroom.cli run photo.png --preview
```

### Dashboard

```bash
python -m cleanroom.web            # http://127.0.0.1:8080
python -m cleanroom.web --port 9000
```

The dashboard is **local-only** (loopback binding + loopback Host check). Drop
a file, pick a strength, and watch the four pipeline stages light up; run
history and an LLM telemetry/cost ledger are one click away.

## Requirements

- Python 3.12 or 3.13
- A virtual environment
- Optional: an OpenAI-compatible endpoint (OMLX on Mac, or vLLM on the DGX)
  for the semantic/rewrite/judge steps. **No model is required for the
  deterministic `safe` path.**

## Configuration

`cleanroom.yaml` holds the active provider, model ids, and pipeline defaults.
Precedence: explicit code args → environment → YAML → built-in defaults.
Secrets (API keys) stay in env vars only (`OMLX_API_KEY` / `DGX_API_KEY`).

```yaml
provider: omlx
omlx:
  base_url: http://127.0.0.1:8000/v1
  model: DeepSeek-V4-Flash-0731-MXFP4-MLX
  model_judge: DeepSeek-V4-Flash-0731-MXFP4-MLX
clean:
  default_strength: safe        # or aggressive
  max_judge_iterations: 3
  judge_score_threshold: 0.80
```

## Project layout

```
cleanroom/
  cleanroom/
    config.py          # pydantic settings (env + YAML + defaults)
    llm.py             # OpenAI-compatible client (OMLX / DGX), single choke point
    files.py           # classify / extract / deterministic remediate / safe atomic writes
    telemetry.py       # append-only JSONL LLM ledger
    pipeline.py        # LangGraph StateGraph orchestrator
    cli.py             # command-line entry point
    phases/
      phase1_analyze.py
      phase2_recommend.py
      phase3_remediate.py
      phase4_qa.py
    web/
      server.py        # stdlib HTTP dashboard (local-only)
      runner.py        # background job runner
      payload.py       # dashboard payload aggregation
      static/index.html
  cleanroom.yaml
  tests/
```

## License

MIT — see [LICENSE](LICENSE).
# Cars24 Reputation Intelligence Agent

Agent 1 of an enterprise Agentic AI system that monitors Reddit discussions
about CARS24 and flags potential reputation risks. Produces structured
JSON/CSV output for downstream agents to consume.

## Setup

```
pip install -r requirements.txt
```

No Reddit credentials are needed — the collector finds discussions via web
search and downloads the pages directly.

The Claude Agent stage needs Anthropic API credentials available in the
environment. Either set `ANTHROPIC_API_KEY`, or run `ant auth login` — the
Anthropic SDK picks either up automatically; nothing project-specific to
configure.

## Run

```
python main.py
streamlit run dashboard.py
```

`main.py` writes `output/report.json` and `output/results.csv`; `dashboard.py`
reads those files, so run the pipeline at least once before opening the
dashboard.

## Pipeline

```
main.py -> collector.py -> claude_agent.py -> risk_agent.py -> report_generator.py -> dashboard.py
```

See [CLAUDE.md](CLAUDE.md) for the full architecture spec and module
responsibilities.

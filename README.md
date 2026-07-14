# 🚗 Cars24 Reputation Intelligence AI

An AI-powered reputation intelligence pipeline that automatically discovers Reddit discussions about Cars24, filters relevant conversations using Claude AI, scores reputation risk, and generates structured reports with an interactive dashboard.

---

## Features

- Collects Reddit discussions using DuckDuckGo and Bing search
- Filters relevant Cars24 discussions using Claude AI
- Scores reputation risk for each discussion
- Categorizes discussions into Low, Medium, High, and Critical priority
- Exports structured JSON and CSV reports
- Interactive Streamlit dashboard for visualization
- Modular agent-based architecture

---

## Project Architecture

```
                +----------------+
                |    main.py     |
                +-------+--------+
                        |
                        ▼
              +------------------+
              |   collector.py   |
              | Collect Reddit   |
              +--------+---------+
                       |
                       ▼
             +--------------------+
             | claude_agent.py    |
             | Relevance Filter   |
             +---------+----------+
                       |
                       ▼
             +--------------------+
             |  risk_agent.py     |
             | Risk Scoring       |
             +---------+----------+
                       |
                       ▼
           +------------------------+
           | report_generator.py    |
           | JSON + CSV Reports     |
           +-----------+------------+
                       |
                       ▼
              +------------------+
              | dashboard.py     |
              | Streamlit UI     |
              +------------------+
```

---

## Tech Stack

- Python
- Anthropic Claude API
- Streamlit
- BeautifulSoup
- Requests
- DuckDuckGo Search
- Bing Search
- Pandas

---

## Installation

Clone the repository

```bash
git clone https://github.com/Sam13-coding/Cars24_reputation_AI.git
cd Cars24_reputation_AI
```

Create a virtual environment

```bash
python -m venv .venv
```

Activate it

Windows

```bash
.venv\Scripts\activate
```

Install dependencies

```bash
pip install -r requirements.txt
```

Configure Claude

Set your Anthropic API key

```
ANTHROPIC_API_KEY=your_api_key_here
```

---

## Running the Project

Run the pipeline

```bash
python main.py
```

Launch the dashboard

```bash
streamlit run dashboard.py
```

---

## Output

The pipeline generates

```
output/
│
├── report.json
└── results.csv
```

---

## Repository Structure

```
Cars24_reputation_AI/

├── collector.py
├── claude_agent.py
├── risk_agent.py
├── report_generator.py
├── dashboard.py
├── main.py
├── prompts.py
├── requirements.txt
├── README.md
└── output/
```

---

## Future Improvements

- Live Reddit monitoring
- Sentiment analysis
- Multi-platform monitoring
- Automatic email alerts
- Trend analysis
- Historical reputation tracking

---

## Author

**Soumyadeep Bhattacharya**

GitHub:
https://github.com/Sam13-coding

---

## License

This project is licensed under the MIT License.
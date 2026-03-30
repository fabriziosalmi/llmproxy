# Analytics View

The Analytics view provides spend tracking and cost analysis.

![Analytics View](/screenshots/soc-analytics.png)

## KPI Cards

| Card | Description |
|------|-------------|
| **Total Requests** | Request count in selected period |
| **Total Spend** | Cumulative cost in USD |
| **Prompt Tokens** | Total input tokens consumed |
| **Completion Tokens** | Total output tokens generated |

## Spend Breakdown

Interactive charts showing cost distribution:

- **By Model**: Which models are consuming the most budget
- **By Provider**: Cost distribution across LLM providers
- **Over Time**: Daily/hourly spend trends

## Budget Tracking

LLMProxy tracks per-model pricing for 30+ models:

- Accurate cost estimation using verified provider pricing
- Daily budget with hard cap and soft warning threshold
- Budget persisted to SQLite (survives restarts)
- Automatic fallback to local LLM when budget is exhausted

## API

```bash
# Spend breakdown
curl "http://localhost:8090/api/v1/analytics/spend?group_by=model&from=2024-01-01" \
  -H "Authorization: Bearer your-key"

# Top models by cost
curl http://localhost:8090/api/v1/analytics/spend/topmodels \
  -H "Authorization: Bearer your-key"
```

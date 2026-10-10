# Analytics

The Analytics screen shows the spend recorded in the proxy's store, as counters and tables. It refreshes every 30 seconds. The screen sends no date range, so the figures cover every record in the store.

## Counters

| Tile | What it shows |
|------|---------------|
| **Total Requests** | Number of recorded requests |
| **Total Spend** | Sum of the recorded cost, in USD |
| **Prompt Tokens** | Sum of input tokens |
| **Completion Tokens** | Sum of output tokens |

## Tables

- **Spend by Model** and **Spend by Provider**: requests, cost and average latency per group, from `GET /api/v1/analytics/spend`
- **Cost Efficiency**: requests, total cost, average cost per request and average tokens per request for each model, from `GET /api/v1/analytics/cost-efficiency`

Each table has a button that saves its rows as a CSV file. The screen has no charts.

## Budget

The cost of a request is computed from the per-model prices in `core/pricing.py`.

- `budget.daily_limit` is the daily cap. A request whose estimated cost would take the day's total over it is refused with HTTP 402
- `budget.soft_limit` is a warning threshold for the budget webhook
- The day's total is saved in the store and read back at startup

The shipped `config.yaml` also sets `budget.fallback_to_local_on_limit`. No code reads that key: there is no fallback to a local model when the budget is exhausted.

## API

```bash
# Spend grouped by model, from a date
curl "http://localhost:8090/api/v1/analytics/spend?group_by=model&from=2026-01-01" \
  -H "Authorization: Bearer your-key"

# Top models by cost
curl http://localhost:8090/api/v1/analytics/spend/topmodels \
  -H "Authorization: Bearer your-key"
```

`group_by` accepts `model`, `provider`, `key_prefix` or `date`; any other value is treated as `model`. `from` and `to` bound the period by date.

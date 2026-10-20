---
name: market-data
description: Look up closing prices for one US trading week and their average.
---
Use the date given as today to find the previous complete trading week: Monday through Friday, skipping the weekend.
Do not calculate unix seconds yourself. Call utc_time with date set to Monday and Saturday as YYYY-MM-DD, and use the returned seconds as period1 and period2.
Then fetch_url:

https://query1.finance.yahoo.com/v8/finance/chart/SYMBOL?interval=1d&period1=PERIOD1&period2=PERIOD2

Align chart.result[0].timestamp with indicators.quote[0].close by position. Drop days whose close is null.
Call utc_time again for each timestamp, with unix set to that value. Use the returned UTC date in the table. Do not change the year yourself.
The average close is the arithmetic mean of those closes. The answer needs each trading day's date and close, the average, and the URL you used.
If the dates are not the requested week, change the period and fetch again. Do not estimate.

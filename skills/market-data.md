---
name: market-data
description: 查美股某个交易周的收盘价和平均值。
---
先用系统给出的「今天」算出上一个完整交易周：周一到周五，跳过周末。
不要心算 unix 秒。用 utc_time，date 填周一和周六的 YYYY-MM-DD，拿返回的秒数做 period1 和 period2。
然后 fetch_url：

https://query1.finance.yahoo.com/v8/finance/chart/SYMBOL?interval=1d&period1=PERIOD1&period2=PERIOD2

把 chart.result[0].timestamp 和 indicators.quote[0].close 按位置对齐，丢掉 close 为 null 的日子。
每个 timestamp 再调用 utc_time，unix 填那个秒数，表格里的日期用返回的 UTC 日期，不要自己改年份。
平均收盘价是这些 close 的算术平均。回答里要有每个交易日的日期、收盘价、平均值，以及这支 URL。
日期对不上要的那一周，就改 period 再取一次。不要估算。

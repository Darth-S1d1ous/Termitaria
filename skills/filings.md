---
name: filings
description: Find a company's latest earnings PDF or shareholder letter and summarize it from the source.
---
Start with web_search. Include the company name, latest quarterly update, PDF, and investor relations.
Prefer the company site or sec.gov, then open the link with fetch_url.
Summarize only revenue, profit, deliveries, and guidance that appear on the page. Cite the URL for each item.
If the page has no page numbers, write "page not labeled". Do not invent page numbers.

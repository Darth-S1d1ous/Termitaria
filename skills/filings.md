---
name: filings
description: 找一份公司最新财报 PDF 或股东信，并按原文摘要。
---
先 web_search，查询里带公司名、latest quarterly update、PDF、investor relations。
优先选公司官网或 sec.gov 的链接，再用 fetch_url 打开。
摘要只写页面上有的营收、利润、交付量和指引。每条都带上那个 URL。
页面没有页码就写「未标注页码」，不要编页码。

# Daily equity data

This directory contains the active demonstration input, separate from the
frozen 2023 research dataset at `../equities.graphml`.

The daily task uses AKShare's `stock_zh_a_hist_tx` with `adjust=""` for securities
and `index_zh_daily_tx` for indices. It requests the complete interval from
2023-01-01 through the configured end date. No custom market-data scraper or
new subtree-selection algorithm is used.

The original input's unnamed observations were verified against all 242 market
sessions from 2023-01-03 through 2023-12-29. They represent opening prices
multiplied by fixed per-node scales. The verified source identifiers, scales,
and original hierarchy are recorded in `../equity_sources.json`. Current
security names are metadata; the original English node identifiers stay stable.
Industry groups that originally summed their children keep that definition.
The independently sourced indices retain their own observed series.

Each release consists of:

- `equities.graphml`: aligned time series, date metadata, and hierarchy.
- `latest-run.json`: result of the existing FDS / Path-greedy / k=20 computation.
- `manifest.json`: source functions and library version, actual observation
  period, file hashes, counts, and missing-session/exclusion records.

The update first builds and validates a candidate and completes the HTSA run.
Only then are the three release files published. The web endpoint checks their
integrity before serving them. The original research datasets and reference
results are never overwritten.

Zero opening-price placeholders are treated as missing observations. Subsequent
missing sessions use the preceding real observation for alignment, with the
imputation count and actual observed end date recorded. A series with no usable
initial observation is rejected. At least 90% of the active stock symbols must
have an actual observation on the market calendar's final session; an empty or
failed source response prevents publication. Confirmed delisted securities are
excluded according to the source configuration, with their identity and reason
retained in the release metadata. The frozen dataset preserves their 2023 history.

The snapshot is for non-commercial academic research and remains subject to
AKShare and underlying-provider conditions described in `../README.md`.
AKShare documentation:
https://akshare.akfamily.xyz/data/stock/stock_quote.html
https://akshare.akfamily.xyz/data/index/index.html

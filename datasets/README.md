# Dataset provenance and release status

The repository's MIT license covers software only. Each data file has its own
source and reuse status. `manifest.json` records byte sizes, SHA-256 checksums,
and machine-readable release decisions.

| File | Series field | Source and transformation | Public redistribution |
| --- | --- | --- | --- |
| `european_regional_gdp.graphml` | `time_series` | Adapted from Eurostat Data Browser tables `nama_10r_2gdp` and `nama_10r_3gdp` (GDP at current market prices), 2014--2021; accessed 2026-08-04. The tabular observations and NUTS labels were converted to a parent--child GraphML forest. | **Cleared with attribution.** Eurostat authorizes reuse of its statistical data for commercial and non-commercial purposes when the source is acknowledged and modifications are identified. Cite the two data codes, access date, and this transformation. |
| `acm.graphml` | `time_series` | The labels resemble the ACM Computing Classification System, but the repository contains no acquisition script, publication-count source, or access date. | **Included with project-author authorization confirmed 2026-08-17.** The upstream source terms remain undocumented, so repository access is not a grant of downstream reuse rights. |
| `equities.graphml` | `d0` | The repository contains no market-data vendor, exchange feed, acquisition method, access date, or redistribution terms. | **Included with project-author authorization confirmed 2026-08-17.** The upstream source terms remain undocumented, so repository access is not a grant of downstream reuse rights. |

## Required Eurostat attribution

> Source: Eurostat, data codes `nama_10r_2gdp` and `nama_10r_3gdp`, accessed
> 2026-08-04. Adapted into a GraphML hierarchy by the HTSA-Explorer authors;
> Eurostat is not responsible for this adaptation.

Eurostat's copyright and reuse notice is:
https://ec.europa.eu/eurostat/help/copyright-notice

## Release and reuse rule

The project author confirmed on 2026-08-17 that all three files may be uploaded
with this repository. The Eurostat-derived file may be reused under the official
terms and attribution above. The ACM and equity files are available to execute
the reported experiments, but their presence does not place them under the MIT
software license or grant permission for downstream redistribution. Complete
their upstream source and license fields in `manifest.json` before asserting a
broader reuse right.

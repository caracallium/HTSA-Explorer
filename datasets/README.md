# Dataset provenance and release status

The repository's MIT license covers software only. Each data file has its own
source and reuse status. `manifest.json` records byte sizes, SHA-256 checksums,
and machine-readable release decisions.

| File | Series field | Source and transformation | Public redistribution |
| --- | --- | --- | --- |
| `european_regional_gdp.graphml` | `time_series` | Adapted from Eurostat Data Browser tables `nama_10r_2gdp` and `nama_10r_3gdp` (GDP at current market prices), 2014--2021; accessed 2026-08-04. The tabular observations and NUTS labels were converted to a parent--child GraphML forest. | **Cleared with attribution.** Eurostat authorizes reuse of its statistical data for commercial and non-commercial purposes when the source is acknowledged and modifications are identified. Cite the two data codes, access date, and this transformation. |
| `acm.graphml` | `time_series` | The labels resemble the ACM Computing Classification System, but the repository contains no acquisition script, publication-count source, access date, or permission record. | **Not cleared. Do not redistribute.** Supply the count-series source and either applicable reuse terms or written permission before release. |
| `equities.graphml` | `d0` | The repository contains no market-data vendor, exchange feed, acquisition method, access date, or redistribution terms. | **Not cleared. Do not redistribute.** Supply the original source and redistribution grant, or replace the file with data under a compatible license. |

## Required Eurostat attribution

> Source: Eurostat, data codes `nama_10r_2gdp` and `nama_10r_3gdp`, accessed
> 2026-08-04. Adapted into a GraphML hierarchy by the HTSA-Explorer authors;
> Eurostat is not responsible for this adaptation.

Eurostat's copyright and reuse notice is:
https://ec.europa.eu/eurostat/help/copyright-notice

## Release rule

A public source mirror or release archive may include
`european_regional_gdp.graphml` with the attribution above. It must exclude
`acm.graphml` and `equities.graphml` until the unresolved fields in
`manifest.json` are replaced by verifiable provenance and permissions. The
files remain in this working copy only so the reported private evaluation can
be checked locally; their presence is not evidence of a redistribution right.
The repository's `.gitattributes` marks both restricted files `export-ignore`,
so a release produced with `git archive` after these changes are committed will
exclude them. This does not make a public clone of the existing Git history
safe: use a clean release archive or scrub the restricted blobs before creating
a public mirror.

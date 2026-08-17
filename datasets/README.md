# Dataset provenance and release status

The repository's MIT license covers software only. Each data file has its own
source and reuse status. `manifest.json` records byte sizes, SHA-256 checksums,
and machine-readable release decisions.

| File | Series field | Source and transformation | Public redistribution |
| --- | --- | --- | --- |
| `european_regional_gdp.graphml` | `time_series` | Adapted from Eurostat Data Browser tables `nama_10r_2gdp` and `nama_10r_3gdp` (GDP at current market prices), 2014--2021; accessed 2026-08-04. The tabular observations and NUTS labels were converted to a parent--child GraphML forest. | **Cleared with attribution.** Eurostat authorizes reuse of its statistical data for commercial and non-commercial purposes when the source is acknowledged and modifications are identified. Cite the two data codes, access date, and this transformation. |
| `acm.graphml` | `time_series` | ACM Computing Classification System labels and hierarchy combined with monthly publication counts from public ACM Digital Library records over the 48-month 2019--2022 study window, then serialized as GraphML; finite series contain 47 or 48 observations. The original access date and acquisition script were not retained. | **Academic research release with attribution.** Project-author release authorization was confirmed 2026-08-17. Cite the ACM CCS and this transformation, and comply with applicable ACM source terms. The file is not licensed under the software's MIT license. |
| `equities.graphml` | `d0` | Daily 2023 Chinese equity series acquired through the open-source AKShare Python interface, organized by exchange and industry into a depth-5 hierarchy, and serialized as GraphML (242 observations per vertex). The original AKShare version, interface function, and access date were not retained. | **Non-commercial academic research release with attribution.** Project-author release authorization was confirmed 2026-08-17. AKShare states that its interface and related data use public sources and are for academic research, not commercial use. Comply with AKShare and underlying-source conditions; the file is not licensed under the software's MIT license. |

## Required Eurostat attribution

> Source: Eurostat, data codes `nama_10r_2gdp` and `nama_10r_3gdp`, accessed
> 2026-08-04. Adapted into a GraphML hierarchy by the HTSA-Explorer authors;
> Eurostat is not responsible for this adaptation.

Eurostat's copyright and reuse notice is:
https://ec.europa.eu/eurostat/help/copyright-notice

## Required ACM and AKShare attribution

For the ACM file, cite the ACM Computing Classification System and identify the
file as an author-created GraphML transformation of monthly 2019--2022
publication counts:

https://dl.acm.org/ccs

For the equity file, cite AKShare and identify the file as an author-created
industry hierarchy based on daily 2023 Chinese equity series acquired through
AKShare:

https://akshare.akfamily.xyz/introduction.html

AKShare's project code is MIT licensed. Its data-use statement separately says
that the interface and related data are for academic research and not for
commercial use:

https://akshare.akfamily.xyz/special.html

## Release and reuse rule

The project author confirmed on 2026-08-17 that all three files may be uploaded
with this repository and released for academic reproduction and research reuse.
The Eurostat-derived file may be reused under the official terms and attribution
above. Academic reuse of the ACM-derived file requires ACM attribution and
compliance with ACM source terms. Reuse of the AKShare-derived equity file is
limited to non-commercial academic research with attribution and remains subject
to AKShare and underlying-source conditions. These source-specific conditions
are separate from the MIT license that covers the HTSA-Explorer software.

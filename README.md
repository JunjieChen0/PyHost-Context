# PyHost-Context

Code, protocols and evidence metadata for a single-key perturbation study of
terminal-environment assumptions in Python test suites.

Author: **Junjie Chen**, School of Computer Science and Engineering, Nanjing University
of Science and Technology. ORCID: [0009-0002-3499-3621](https://orcid.org/0009-0002-3499-3621).

## What this repository contains

| Path | Contents |
|---|---|
| `docs/` | Protocol v1.0/v1.1, evidence consolidation, positioning dossier REV2 |
| `tooling/` | Frozen runner and verification utilities |
| `scripts/` | Batch drivers, adjudicators and recomputation scripts |
| `metadata/` | Signed accounting snapshots, S2 closure, registration addendum, read chains, manifests |
| `manuscript/` | Submission-material drafts and the positioning dossier |
| `release/` | Package manifest and checksums for this public snapshot |

## What is not here

Raw run logs, virtual environments, wheelhouses and restored source trees are **not**
in Git. The frozen evidence archive will be deposited in Zenodo. The DOI and repository
link will be added here once assigned.

## Headline results (bounded)

- **30 research project names**: 25 primary (18 exploration + 7 validation) and 5 in a
  separately admitted supplementary layer.
- **240 screened project-condition units**; recovery blocks were run only on selected
  qualifying units, not on every screened unit.
- **8 registered project-condition units**; after the S2 recovery round: **E4 4,
  E3 1, E2 3**.
- **3,086 registered research calls** after S2 (3,014 suite sessions + 72 witness
  calls). This is a workload count, not a statistical sample size.
- The study certifies **no functional defect**, makes **no ecosystem-prevalence
  estimate**, and does not equate the number of units with a number of independent
  mechanisms.

## Reproduce

The accounting and evidence checks are read-only over closed records and do not execute
candidate code. Python 3.11.9 is the frozen interpreter. See `docs/` for the protocol
and evidence boundaries, and `scripts/` for the recomputation entry points.

## Status

Research artifacts frozen on 2026-10-20. A manuscript is in preparation for *Software
Quality Journal*; it has not been submitted yet.

## License

A license has not been selected yet. Until one is added, the default is all rights
reserved. See `LICENSE_STATUS.md`.

## Contact

Junjie Chen — junjie_chen@njust.edu.cn

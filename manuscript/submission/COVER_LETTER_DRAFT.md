# Cover Letter (draft)

Dear Editor-in-Chief,

I am submitting the manuscript "Terminal Environment Keys and Test Outcomes in Python
Test Suites: A Single-Key Perturbation Study" for consideration as an Original Paper in
*Software Quality Journal*.

The manuscript studies a practical measurement problem: Python test suites can change
outcomes when only an ambient terminal-environment key changes, without any change to
the code under test. The keys examined are `COLUMNS`, `TERM` and `NO_COLOR`. These keys
are part of the environment in which tests run, but test suites rarely establish them
explicitly, and assertions written against rendered output can therefore depend on them.

The paper makes two contributions. First, it operationalizes a single-key perturbation
protocol: screening covers eight keys per project; for each selected qualifying unit,
the unit is committed before execution and run as A1–B–A2 blocks under a two-block
schedule; and an E0–E4 ladder separates a reproduced flip set from baseline
reproduction, identity, stage, recovery and reading-chain evidence. The two blocks are
executed separately and are not treated as statistically independent samples. Second,
it reports a bounded empirical result: across a download-rank quota sample of 25
primary projects (200 units) and a separately admitted five-project supplementary layer
(40 units), eight project–condition units were registered at E2 or above; after the S2
recovery round, four are at E4, one at E3, and three remain at E2 because recovery was
not evaluable. In the five supplementary units the keys are read through rich 15.0.0
along three paths; an earlier rich-argparse `COLUMNS` unit reads through the standard
library `optparse`. A mutation batch retained nine readings, registered one independent
original-suite blind spot and signed zero same-context degradations; repair endpoints
are reported separately as 6/6 for exploration and 3/3 for the limited validation
endpoint.

The manuscript does not claim ecosystem prevalence, an independent mechanism count, or
a functional defect. Its contribution is the reusable operationalization and the
explicit evidence boundaries, which I believe fit the journal's interest in software
testing, quality measurement and empirical case studies.

The submission is not under consideration elsewhere and has not been posted as a
preprint. The author declares no competing interests. The research received no
specific grant from any funding agency in the public, commercial, or not-for-profit
sectors. The frozen evidence bundle, commitment hashes, ledgers and recomputation
scripts will be deposited in Zenodo, and the code and documentation snapshot is
available at https://github.com/JunjieChen0/PyHost-Context. [[TODO: insert the Zenodo
DOI before submission.]]

Thank you for considering the manuscript.

Sincerely,

Junjie Chen
School of Computer Science and Engineering
Nanjing University of Science and Technology
junjie_chen@njust.edu.cn

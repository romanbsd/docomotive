# OCR post-correction papers

Primary-source PDFs saved on 2026-10-13 for the local pipeline literature review.
The ten copies include author preprints, institutional copies, publisher/anthology PDFs and the user-supplied PoCoTo paper;
versions are identified below. Author notices and licenses remain in the PDFs.
[`manifest.json`](manifest.json) records original URLs, byte sizes and SHA-256
checksums. The PDFs are research references, not installed dependencies.

Read the [review and local evaluation](../classical-ocr-correction.md) before
using the reported accuracy figures to justify a pipeline change.

| Local copy | Citation / version | Useful component |
|---|---|---|
| [Evershed & Fitch](evershed-fitch-2014.pdf) | [DATeCH 2014](https://doi.org/10.1145/2595188.2595200), author PDF | Weighted multi-character channel, contextual search |
| [Mei et al.](mei-et-al-2016.pdf) | [2016 preprint](https://arxiv.org/abs/1611.06950); extended [2018 journal article](https://doi.org/10.1016/j.ipm.2018.06.001) is a separate version | Candidate recall and feature-based ranking |
| [Schulz & Kuhn](schulz-kuhn-2017.pdf) | [EMNLP 2017](https://aclanthology.org/D17-1288/) | Modular proposals, text-internal vocabulary, domain adaptation |
| [Drobac & Lindén](drobac-linden-2020.pdf) | [IJDAR 2020](https://doi.org/10.1007/s10032-020-00359-9), institutional copy | OCR voting, context-sensitive weighted finite-state rules |
| [Afli et al.](afli-et-al-2016.pdf) | [LREC 2016](https://aclanthology.org/L16-1153/) | Aligned-data SMT; warning about period-mismatched language models |
| [Englmeier et al.](englmeier-et-al-2022.pdf) | [JLCL 2022](https://aclanthology.org/2022.jlcl-1.1/) | Separate reranking and replacement decision, training/evaluation design |
| [Vobl et al., PoCoTo](PoCoTo-2014.pdf) | [DATeCH 2014](https://doi.org/10.1145/2595188.2595197), user-supplied PDF | Source concordances, error-series batch review, separate historical/OCR channels |
| [Nguyen et al. survey](nguyen-et-al-2021-survey.pdf) | [ACM Computing Surveys](https://doi.org/10.1145/3453476), online 2021; volume 54(6), 2022, author PDF | Taxonomy, evaluation and further bibliography |
| [Lyu et al.](lyu-et-al-2021.pdf) | [TACL 2021](https://aclanthology.org/2021.tacl-1.29/) | Character neural baseline, reserved for later comparison |
| [Ramirez-Orta et al.](ramirez-orta-et-al-2021.pdf) | [2021 preprint](https://arxiv.org/abs/2109.06264); AAAI 2022 | Overlapping-window neural correction and voting, reserved for later |

[PoCoTo's paper](PoCoTo-2014.pdf) was supplied by the user after publisher PDF
retrieval failed. Its page-5 footnote links to
[`thorstenv/PoCoTo`](https://github.com/thorstenv/PoCoTo) and the original project
site. Those pages direct development to
[`cisocrgroup/PoCoTo`](https://github.com/cisocrgroup/PoCoTo). The full paper and
selected successor source files have now been reviewed.

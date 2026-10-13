# OCR post-correction papers

Primary-source PDFs saved on 2026-10-13 for the local pipeline literature review.
The initial ten copies include author preprints, institutional copies, publisher/anthology PDFs and the user-supplied PoCoTo paper;
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

## Adaptive profiling and review-efficiency follow-up

Nine additional full texts were saved for the [follow-up review](../adaptive-ocr-profiling.md).
That follow-up brought the archive to 19 PDFs. Only the Xu & Smith repeated-passage full text remains unavailable;
the review lists them and distinguishes abstract-only findings.

| Local copy | Citation / version | Useful component |
|---|---|---|
| [Fink, Schulz & Springmann](fink-schulz-springmann-2017.pdf) | [2017 arXiv preprint](https://arxiv.org/abs/1701.05377) | Adaptive lexical feedback, unexplained tokens, separate historical/OCR channels |
| [Abdulkader & Casey](abdulkader-casey-2009.pdf) | [Google Research, ICDAR 2009](https://research.google/pubs/low-cost-correction-of-ocr-errors-using-learning-in-a-multi-engine-environment/) | Review prioritization, image-based clustering, batch feedback |
| [Poncelas et al.](poncelas-et-al-2020.pdf) | [LT4HALA 2020](https://aclanthology.org/2020.lt4hala-1.7/) | Vocabulary-aware proposals and explicit miscorrection examples |
| [Suissa et al.](suissa-et-al-2020.pdf) | [2021 manuscript](https://arxiv.org/abs/2106.06831), journal publication 2020 | Task size, images and two-stage review; synthetic errors |
| [Fischer & Puppe](fischer-puppe-2026.pdf) | [LREC 2026](https://aclanthology.org/2026.lrec-1.559/) | AdaptOCR dictionaries, transcription view and PAGE annotation |
| [Packer](packer-2011.pdf) | [HIP 2011](https://doi.org/10.1145/2037342.2037354), archived author copy | Joint information extraction/error detection for structured records |
| [Springmann et al.](springmann-et-al-2014-latin.pdf) | [DATeCH 2014](https://doi.org/10.1145/2595188.2595205), author PDF | Historical Latin lexical resources and font adaptation |

The user supplied two published journal PDFs after online retrieval failed:

| Local copy | Citation / version | Useful component |
|---|---|---|
| [Reffle & Ringlstetter](reffle2013.pdf) | [Pattern Recognition 2013](https://doi.org/10.1016/j.patcog.2012.10.002), user-supplied journal PDF | Unsupervised two-channel profiles, conditional candidate ranking and timed batch review |
| [Reffle](reffle2011.pdf) | [Natural Language Engineering 2011](https://doi.org/10.1017/S1351324911000039), user-supplied journal PDF with publisher cover | Virtual variant lexicon, historical/OCR traces and bounded finite-state candidate search |

Both full texts have now been reviewed; their algorithms were not executed.

## Simple algorithms and evaluation follow-up

Four additional primary-source PDFs bring the archive to **23 verified PDFs**.
See the [prioritized plan and new experiments](../ocr-improvement-plan.md).

| Local copy | Citation / version | Useful component |
|---|---|---|
| [Furrer & Volk](furrer-volk-2011.pdf) | [LaTeCH 2011](https://aclanthology.org/W11-4115/) | Corpus-derived lexical resources, shared-trigram fallback, warnings about recurring errors |
| [Reynaert](reynaert-2016.pdf) | [LREC 2016](https://aclanthology.org/L16-1154/) | TICCL candidate recall, splits/merges, foreign text and representative evaluation |
| [Smith](smith-2011-limits.pdf) | [ICDAR 2011 author copy](https://research.google/pubs/limits-on-the-application-of-frequency-based-language-models-to-ocr/) | Correct-word damage from frequency models; classifier-specific evidence |
| [Neudecker et al.](neudecker-et-al-2021-evaluation.pdf) | [HIP 2021 author copy](https://primaresearch.org/publications/HIP21_CNeudecker_OcrEvalSurvey) | Alignment, normalization, layout and evaluation provenance |

Methods and evaluation sections were inspected. The new experiment borrows
individual building blocks; it does not reproduce these complete systems.

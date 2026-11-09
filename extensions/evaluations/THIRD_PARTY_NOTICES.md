# Third-party notices

The Evaluations extension is licensed under Apache-2.0. Bundled font files retain their original SIL
Open Font License 1.1 and copyright notices:

| Asset | Upstream project | Included license |
| --- | --- | --- |
| Doto | [Doto](https://github.com/oliverlalan/Doto) | [doto-OFL.txt](evaluations/public/fonts/doto-OFL.txt) |
| Space Grotesk | [Space Grotesk](https://github.com/floriankarsten/space-grotesk) | [space-grotesk-OFL.txt](evaluations/public/fonts/space-grotesk-OFL.txt) |
| Space Mono | [Google Fonts](https://github.com/google/fonts/tree/main/ofl/spacemono) | [space-mono-OFL.txt](evaluations/public/fonts/space-mono-OFL.txt) |

Python dependencies are resolved from `uv.lock`, among them pydantic-evals and Pydantic AI (MIT) for
the evaluation engine and the LLM judge, and PyIceberg (Apache-2.0) and PyArrow (Apache-2.0) for the
result tables. Their upstream licenses remain applicable. Container images built here include the
installed dependencies and their distribution metadata.

# Provenance

Source: author-owned worksheet extension, snapshot `826d2a4`. No source repository history or private datasets are imported.

| Source relative path | Commit | Source SHA256 | Destination | Changes |
|---|---|---|---|---|
| `adapters/deeptutor/question_package.py` | `826d2a4` | `ba5aa8d0804906438a52ec713df3f8cf0a671f71193b6de0ded8fcbe188050fb` | `src/physics_worksheet_mcp/bank/package.py` | byte-identical |
| `src/physics_latex_export/contract.py` | `826d2a4` | `214c2c1f0034949c08f1677c9811dcd507667af0a3339124566467256521e71e` | `src/physics_worksheet_mcp/render/contract.py` | byte-identical |
| `src/physics_latex_export/renderer.py` | `826d2a4` | `3b9710737fbb962632f21f87a4b969e91764177c930796eee221064af6fd61ce` | `src/physics_worksheet_mcp/render/renderer.py` | Original rendering/QA logic; added compile and QA timing fields; use canonical pymupdf import |
| `src/physics_latex_export/safety.py` | `826d2a4` | `ca7a1526bf03cb3867ea26478ddf940d11890a15ec73c10ba7dc074689054060` | `src/physics_worksheet_mcp/render/safety.py` | byte-identical |
| `src/physics_latex_export/adapters/compiler.py` | `826d2a4` | `59e577db671417973467df0d242c137a247d9df16cbdae5a979c5b347fe1aa65` | `src/physics_worksheet_mcp/render/adapters/compiler.py` | byte-identical |
| `question-package.schema.json` | `826d2a4` | `976761fb70849ab31f3d176fe3f3e06dea4db5519bc2e31b28cc6557bde51b3b` | `src/physics_worksheet_mcp/schemas/question-package.schema.json` | byte-identical |
| `exam-spec.schema.json` | `826d2a4` | `c7b79dfcd0618b8bb6a1808d71222df38f6f48e0c1300819b46cdefc710bfc0f` | `src/physics_worksheet_mcp/schemas/exam-spec.schema.json` | byte-identical |
| `answer-package.schema.json` | `826d2a4` | `79d3a8a89d7b5fef853da5303b7a07061eb86776e85f07ff50321fa7d9d111fb` | `src/physics_worksheet_mcp/schemas/answer-package.schema.json` | byte-identical |
| `adapters/deeptutor/answer_package.py` | `826d2a4` | `c1d3c1421b5fc9731130e8dd377efc46cd0fdff6db094da7ec679c1522b9b1db` | `src/physics_worksheet_mcp/bank/answers.py` | Validator only; local imports and packaged schema; canonical pymupdf import; no DT/admin runtime |
| `adapters/deeptutor/provider_registry.py` | `826d2a4` | `32e45713c905e6e7df5685f6e8c71f0abec86c76883641b8e15da398a834c6d7` | `src/physics_worksheet_mcp/bank/legacy.py` | Import-time only; local package loader; private generic default |

Synthetic fixtures and low-level tests were reviewed and imported from the same snapshot. Imports and test paths are adapted. Higher-level modules are newly written from the documented behavior. `native_limits.py` is represented by the same budget formula in the worker supervisor; its DT error dependency is removed.

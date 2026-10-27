---
status: accepted
date: 2026-09-24
superseded-by:
decision-drivers:
  - The preview draws in the browser (TypeScript) and the export runs in Python, and a scene must animate on the same frame in both.
  - Calling one implementation from the other side would put a cross-language hop on every preview frame or every export.
---

# The scene spec rule is written twice and held together by parity fixtures

Which scenes animate, from which frame and with which resolved shot is needed by the browser preview and
by the Python export. We implement that rule twice, in TypeScript for the preview and in Python for the
export, and hold the two identical with shared fixtures that both test suites assert, instead of one
implementation called across the language boundary.

**Consequences**

- A change to the rule is two edits plus a fixture change; a one-sided edit fails a parity test.
- A fixture is extended honestly and regenerated only when the rule itself changes, never to make a test
  pass.
- The same pattern holds acting, staging, cast shots, caption pages and timeline length, each with its own
  `tests/fixtures/*_parity.json`, and the draw environment (fonts) is part of parity too.

**Evidence**

- `ui/src/v3/story/shot/specs.ts`: `sceneSpec` and `animatedSceneSpecs` derive preview shots.
- `pipeline/scene_render.py`: `scene_specs` derives export shots.
- `tests/test_scene_specs_parity.py`: `test_export_scene_specs_match_the_shared_parity_fixture`
  checks `tests/fixtures/scene_specs_parity.json`.
- `ui/test/sceneSpecsParity.test.ts`: `describe("scene render spec parity with the export")` checks the same fixture.
- `ui/test/fontParity.test.ts`: `describe("preview/export font parity")` checks the shared drawing environment.

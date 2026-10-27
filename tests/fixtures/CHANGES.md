# Fixture regeneration

Run `python tests/fixtures/generate.py` from the repository root using the
repository Python environment. The generator uses disposable state and writes
deterministic UTF-8 outputs. Run it again with `--check` to verify that all
fixtures are current without writing anything.

Edit fixture sources under `inputs/`, then regenerate their outputs. Never edit
expected results in generated fixtures. Python computes scene requests, cast
resolution, thumbnail requests, caption pages, script timing and insertion
results; TypeScript tests independently compare those results. Static document
and stock-response samples also have sources under `inputs/` and are emitted by
the same generator. Preserve the behavior each case verifies when changing its input.

For pixel recordings, run `node scripts/update-goldens.ts` from `motion-core/`.
It records all frame suites for the current platform only. Frame definitions
live in `motion-core/test/*-frames.ts`; bundled fonts are registered through
`motion-core/scripts/node-env.ts`. Missing required fonts or a mismatched font
fingerprint must be resolved before recording. Review changed frame definitions
and every changed recording together.

The reference film is a seven-beat museum visit: title, meeting point, route board,
ticket screen, gallery pan, a question and a word-mark sign-off. Sheet scripts
demonstrate features separately. The poster sample contains one Lens performer.
The word input's timingRecipe defines a repeating duration cycle, constant gaps,
four designed pauses and one unaligned word; no recording supplies its timestamps.
Scene inputs retain explicit cuts to exercise word-to-timeline mapping.

The staging and feature parity fixtures organise layout, camera, board, mark
and member-label checks into independent single-scene documents. Each case is
checked by Python export and TypeScript preview. Placeholder rigs and an end
card with an empty mark have a separate robustness frame group and platform recording.

List every changed input, generated fixture and platform recording in the
pull request description. After regeneration, run Python, motion-core and UI tests, build
the UI and run the fixture browser harness. A second generator run must report
no drift.

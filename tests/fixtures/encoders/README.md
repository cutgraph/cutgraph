`lgpl.txt` and `gpl.txt` are complete, unedited stdout from
`ffmpeg -hide_banner -encoders`. The LGPL listing comes from the pinned portable
runtime; the GPL listing comes from the source runtime. Both use FFmpeg 8.1.

`nvidia.txt`, `intel.txt`, `amd.txt` and `none.txt` are capability subsets of the
recorded LGPL listing: only H.264 encoder rows were removed. They model builds
with restricted encoder support, not hardware detection. An encoder listing
describes the build, so an installed GPU cannot change it. Device availability
is checked by attempting an encode.

`../encoder_ladder_parity.json` pins expected order, all four argument profiles,
initialization signatures, selection and ordinary errors independently of the
runtime definition. Change expectations only when deliberately changing the
encoding contract.

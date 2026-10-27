# Changelog

All notable changes to Cutgraph will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] - 2026-10-27

### Added

- Transcript-driven Story editing with Script, Board, Timeline and Source lenses.
- Motion-core animation shared by preview and export, with captions in exported videos.
- An unsigned Windows installer with SHA-256 checksums, build attestations and
  guidance for Windows security prompts.
- A portable runtime bundle with Python, Node.js, FFmpeg and bundled fonts.
- On-demand CPU and NVIDIA transcription add-ons, plus an optional Assistant
  add-on using your own Anthropic API key.
- A first-run welcome screen and Settings → Add-ons for installation and management.
- An in-app stable update check with an automatic preference enabled by default
  and an off switch. Network checks remain disabled until the release verification
  key is configured.
- An LGPL FFmpeg encoder ladder using hardware encoders and Windows Media
  Foundation, while source installations retain libx264 support.
- Fair Source licensing, community policies, third-party licence notices, a
  CycloneDX software bill of materials and accompanying FFmpeg dependency sources.
- An API endpoint for the application version.

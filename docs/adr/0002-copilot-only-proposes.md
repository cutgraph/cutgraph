---
status: accepted
date: 2026-09-24
superseded-by:
decision-drivers:
  - An edit Claude makes must be as reviewable and undoable as one a person makes.
  - The co-pilot runs with tools using a protected Anthropic API key, so its reach must be bounded by design, not by prompt.
---

# The co-pilot only proposes

The co-pilot could have been given tools that edit a video directly. Instead every co-pilot tool only
builds a proposal against the current revision and stores it as pending, a permission gate denies it file
writes and the shell, and a person's Accept applies the proposal through the same revision-gated write as
any other edit.

**Consequences**

- Nothing the co-pilot does changes a video until Accept; a proposal made on an older revision is marked
  stale instead of being misapplied.
- The co-pilot learns a proposal's fate on its next turn, so it describes its work as proposed, never as
  done.

**Evidence**

- `scripts/agent_host.py`: `_do_copilot` calls `copilot.propose` and stores a pending proposal;
  `_do_propose_patch` follows the same proposal path.
- `pipeline/copilot.py`: `Proposal` and `propose` describe edits against the current document.
- `scripts/agent_host.py`: `_make_can_use_tool` and `_allowed_tools` enforce the tool boundary.
- `studio/routes/chat.py`: `api_proposal_accept` checks the revision and commits the accepted edit.
- `config.py`: `AGENT_MODES` offers default and read-only plan modes.

---
id: to.launch-boat
type: test-obligation
title: Launch limit check
status: derived
confidence: high
rev: 1
schema: 1
rationale: A generated requirement from the launch action.
fixtures:
  - "launch-one-boat"
tests:
  - "LaunchLimitTests"
---

# Launch limit check

Checks that a second launch in one turn is refused.

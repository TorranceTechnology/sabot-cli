---
id: act.launch-boat
type: action
title: Launch a boat
status: mapped
confidence: high
rev: 1
schema: 1
launch-cost: 1
tags:
  - "launch"
source:
  - "dw1#5.1"
  - "dw1#5.1a"
occurs-in:
  - "[[ph.sail]]"
requires:
  - "[[zone.hand]]"
triggers:
  - "[[tp.boat-launched]]"
implemented-by:
  - "[[con.launch-boat-command]]"
tested-by:
  - "[[to.launch-boat]]"
---

# Launch a boat

The mover pays a card, then puts a boat from hand into the harbour. Only one launch is allowed per turn.

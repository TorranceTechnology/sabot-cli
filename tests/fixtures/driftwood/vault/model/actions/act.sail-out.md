---
id: act.sail-out
type: action
title: Sail a boat out
status: mapped
confidence: high
rev: 1
schema: 1
tags:
  - "cargo"
  - "scoring"
source:
  - "dw1#5.4"
occurs-in:
  - "[[ph.sail]]"
requires:
  - "[[zone.harbour]]"
---

# Sail a boat out

A loaded boat departs for points equal to the weight aboard. Afterwards the cargo is discarded and the vessel goes back to its owner's hand.

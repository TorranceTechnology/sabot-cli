---
id: rp.sink-check
type: rule-process
title: Overload check
status: mapped
confidence: high
rev: 1
schema: 1
source:
  - "dw1#5.3"
part-of:
  - "[[ph.sail]]"
invokes:
  - "[[proc.wreck-boat]]"
---

# Overload check

A standing check that looks for any boat bearing too much weight.

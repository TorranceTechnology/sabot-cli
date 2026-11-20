---
id: rel.launch-needs-card
type: relation
title: Launching needs a card from hand
status: mapped
confidence: high
rev: 1
schema: 1
relation-type: requires
condition: the card paid comes from the same hand as the boat
source:
  - "dw1#5.1"
references:
  - "[[act.launch-boat]]"
  - "[[zone.hand]]"
---

# Launching needs a card from hand

Reifies the launch cost as a requirement tied to the hand.

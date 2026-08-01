---
name: migration-constraints
description: Hard sequencing rules for migration-wave planning. Consult for ANY
  migration ordering, modernisation sequencing, or cutover planning task, even
  when constraints aren't mentioned explicitly.
---

# Migration sequencing constraints (deterministic — the engine enforces them)

1. **Dependency order**: a system's dependencies migrate in the SAME wave or
   earlier. Violation invalidates the whole sequence.
2. **Wave size ≤ 3** systems (cutover-team capacity).
3. **"none" downtime window** systems (e.g. Auth Gateway) may not share a wave
   with any "sun-02"-window dependent — their cutover styles conflict.
4. Completeness: every catalog system appears exactly once.

A sequence failing any rule is dead before scoring. Your job when generating is
to not waste candidates on dead sequences.

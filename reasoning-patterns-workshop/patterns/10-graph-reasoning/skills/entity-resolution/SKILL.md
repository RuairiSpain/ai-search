---
name: entity-resolution
description: Rules for identity handling in graph investigations. Consult for
  ANY task that joins, matches, or links entities — fraud rings, dependency
  chains, ownership networks — even when "entity resolution" isn't mentioned.
---

# Entity resolution discipline

- **Ids are truth; names are hints.** Two records named "John Smith" are two
  entities until an id-level link proves otherwise. Merging by name is the
  single most damaging error in graph reasoning (§15: "poor resolution poisons
  everything downstream").
- **Type the link.** "Connected" is not a finding; "shares payment instrument
  P1" is. Cite the relation and both ids.
- **Weigh by relation semantics.** A shared registrar address links thousands
  of legitimate companies; a shared card does not.
- **Absence is evidence.** "No path within 3 hops over high-weight relations"
  is a reportable, useful result.

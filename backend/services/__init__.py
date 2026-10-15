"""Business / financial-logic services layer.

Modules here own the domain math (ledger folds, net worth, position hydration,
commitment funding, statement reconciliation). They sit between the api/ai
entrypoints and the db/ repositories: api/ai -> services -> db -> core.

No re-exports at the package level — import the concrete module
(``backend.services.ledger`` etc.) so the real dependency edges stay visible to
import-linter.
"""

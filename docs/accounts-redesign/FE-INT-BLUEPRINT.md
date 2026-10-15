# FE-INT blueprint — `AccountsTab` orchestrator (ports prototype `main.jsx` `App`)

The orchestrator owns state + wires FE0's `useAccountsLedger` mutations to the FE1/FE2/FE3
component callbacks. It is the 1:1 port of the prototype `App` (minus the frozen shell bits).

## Render tree (inside the existing RouterShell content area — NOT full viewport)
```
<div class="acct-shell" grid={collapsed ? '1fr' : `${sideW}px 1fr`} paddingRight={luciusSide? : 0}>
  {!collapsed && <Sidebar … onAdd onManagePayees onResizeStart onToggleCollapse />}        // FE1
  <main class="acct-main">
    {accounts.length===0 ? <ZeroState/>                                                    // FE1 (or inline)
     : <>
        {selectedAccount ? <AccountHeader …/> : <Overview …/>}                              // FE1
        <div class="acct-sticky"><Filters/><ActiveChips/></div>                             // FE2 (SearchFilter)
        <Register txns={filtered} showAccount={selectedId===null} … onAdd=inlineAdd … />    // FE2
       </>}
  </main>
  {selectedIds.size>0 && <BulkBar …/>}                                                      // FE2
  <AccountModal …/>            // FE3   (onDescribe→hook.describeAccount; onSave→hook.saveAccount)
  <TxnDetail …/>               // FE3   (onSave→updateTxn; onDelete→confirm→deleteTxn; onMarkReviewed→updateTxn{needs_review:false}; onLoadActivity→GET /activity)
  {confirm && <ConfirmModal …/>}        // FE3
  {connectAcct && <ConnectModal …/>}    // FE-INT ports this (~45 lines from main.jsx 812-856; bank-connect review → coming-soon/connectors)
  {importState && <ImportModal …/>}     // FE3   (onImport→hook.importTxns via /api/import)
  {showPayees && <PayeesModal …/>}      // FE3
  {catModal && <AddCategoryModal …/>}   // FE3   (onSave→createCategory→/api/categories)
  {toast && <Toast/>}                   // FE3
</div>
// SKIP entirely (frozen/consumed): AppNav, LuciusBar, LuciusSidePanel, TweaksPanel.
// The existing RouterShell nav + LuciusDock already wrap this tab.
```

## State the orchestrator owns (port from main.jsx 268-466)
`selectedId` (null=Overview), `selectedIds` (Set), `filter` {tokens, preset, cfM/cfY/ctM/ctY},
`acctModal`, `detailTxn`, `confirm`, `connectAcct`, `importState`, `showPayees`, `catModal`,
`adding`, `menuOpen`, `toast`; sidebar `sideW`+`collapsed`+`railPinned` + **responsive
auto-collapse** (resize listener, hysteresis collapseAt 700 / +80; pin on manual toggle); column
state lives in Register. Derived: `groups,totals,netWorth,cashFlow` (from hook), `filtered`
(client token-match over loaded txns), `inScopeCount`, `selList/selTotal`, `suggest`
{categories,payees,tags,accounts}, `acctStats/allStats/dueInfo`.

## Mutations → hook mapping (server-authoritative balances; refetch after writes)
| prototype App fn | wire to |
|---|---|
| `inlineAdd(data)` | `hook.createTxn` (map camel→snake via data.ts adapter); returns false on invalid |
| `updateTxn(id,patch)` (inline edits, drawer save, cleared toggle, mark-reviewed) | `hook.updateTxn` / `hook.toggleCleared` |
| `applyBalance` | **dropped** — balances are server-side; after any write `hook.refresh()`/`refreshTxns()` |
| bulk category/cleared/note/duplicate/move/delete/export | `hook.bulkUpdate` (category/cleared/note via patch; needs_review; to_account_id move) / `hook.bulkDelete` / client CSV export / duplicate = N× createTxn |
| `saveAccount(form)` | `hook.saveAccount` (form→AccountCreate/Update adapter) |
| `reconcile(account,real)` | `hook.reconcile` |
| `archive(a)` | confirm → `hook.archiveAccount` |
| `importTxns(drafts,accountId)` | handled inside ImportModal via `hook.importTxns`/`/api/import` |
| payee fns (changePayee/merge/add/delete/applyRules) | hook payee ops / PayeesModal calls |
| `createCategory(data)` | `POST /api/categories` via hook |
| `requestCategory(prefill,kind,onDone)` | opens `catModal`; onDone(key) returns the new key to the field |

## Filter strategy (faithful + correct with real data)
Fetch the **scoped** txn set server-side (account scope + date-range preset → `/api/transactions`
filters) with a generous limit; do **search-token** filtering + sort + infinite-scroll windowing
(REG_PAGE=60) **client-side** over the loaded set — exactly the prototype. "Select all N matching"
operates on the loaded+matching set. If pagination caps bite (very large accounts), note in
DIVERGENCES.md and load-more on scroll.

## Layout adaptation
Prototype assumes it owns the viewport (`--onw-nav-w`, toast/bulk-bar centered by navW). In the app
it lives in RouterShell's content region: drop navW math, center toast/bulk-bar within the content
column, ensure the sticky filter bar + register scroll within the tab (not the window). Keep the
sidebar resize/collapse + drawer/lucius push behavior relative to the content region.

## Router swap
`frontend/src/router.tsx` / `RouterShell`: point `/accounts` (+ `/accounts/payees`) at `AccountsTab`
instead of `Cash`. Keep tab id `cash`/label "Accounts". `/accounts/payees` opens the PayeesModal.
Leave `Cash.tsx` until verify passes (cleanup deletes it).

## CSS debt
FE0 set `.css-debt.json` deadCount=202 (the ported classes start "dead"). As components consume
them the true dead-count drops; FE-INT resets deadCount to the real remaining (target 0) so
`lint:classes` stays honest.

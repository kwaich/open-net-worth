# Card-edge accent audit

Status: implementation specification for task 114. Owner sign-off remains required.

## Rule set

- Content cards use a neutral one-pixel outline. Semantic color never runs along a card edge.
- Status cards reuse an existing dot, icon, badge, label, opacity, or restrained whole-border/surface tint. Color is supplementary.
- Compact repeated identity rows may use a 6px marker beside the identity when the color represents a stable, scannable group. The marker is decorative (`aria-hidden`) and the group remains named in surrounding UI.
- Selection uses a border/surface tint; keyboard focus uses a two-pixel offset focus ring. Neither borrows warning/error color.
- True banners use an icon plus a one-pixel tinted outline and restrained surface tint. They do not add a thick rail.
- Structural dividers, timelines, chart markers, resize handles, navigation boundaries, and blockquotes are outside this card convention.

## Candidate decisions

| Candidate | Purpose | Decision |
| --- | --- | --- |
| `.intel-story-stripe` | 2 status/urgency | Remove. Existing unread dot, type pill, status dot/text, lifecycle opacity, neutral outline, selected tint, and focus ring carry the states. |
| `.mk-hold-stripe` | 1 identity/category | Replace with a 6px dot positioned beside the ticker inside the existing identity inset. It uses the section group color and changes neither row height nor ticker start position. |
| `.agent-improvement` | 2 status | Remove green/amber rail. Keep neutral card outline; blocked uses a restrained whole-border/tint and existing status language. |
| `.inbox-item--actionable` | 2 urgency | Remove red rail. Use a restrained whole-border/tint; existing actionable content remains the non-color signal. |
| `.skill-degraded-banner` | 4 warning/banner | Redesign as an error callout: bad-tinted one-pixel outline and surface plus existing “Unavailable” label. |
| `.upload-installed-banner` | 4 warning/banner | Redesign as a warning callout with a tinted outline/surface and existing warning text. |
| `.acct-alert` / `.acct-alert.paid` | 4 warning/banner | Remove amber/green rails. Retain the mark, text, full tinted outline, and surface. |
| `.acct-detail-review` | 4 warning/banner | Remove rail; use the existing “Needs your review” heading with a warning-tinted outline/surface. |
| `.imp-pair-row` | 2 status | Remove strong/review/weak rails. Use whole-border/tint variants; existing match verdict and flags remain primary. |
| `.imp-insync` | 4 status banner | Remove green rail; retain check/text, green-tinted outline, and surface. |
| `.imp-verdict2` | 6 decorative/redundant | Remove the heavier neutral edge; retain the ordinary one-pixel outline. |
| `.mk-flag` | 4 warning/banner | Remove amber rail; retain warning icon/text with tinted outline/surface. |
| `.acct-form-role-warn` | 4 warning/banner | Remove rail; use a warning-tinted one-pixel outline/surface with existing text. |
| `.conn-dir__regional-guide` | 5 hierarchy/structure | Retain intentionally. It is an informational aside, not a card, and the edge denotes nested hierarchy rather than semantic status. |
| `.tax-disclaimer` | 4 warning/banner | Remove rail; retain warning icon, warning-tinted one-pixel outline, and surface. |
| `.fire-formula` | 6 decorative/redundant | Replace accent rail with a restrained whole outline/tint; the formula/value already establishes hierarchy. |
| `.builder-thesis` | 6 decorative/redundant | Remove accent rail; retain its neutral outline and existing marker. |
| `.builder-authority__explain` | 2 status | Replace good/warn edge with a restrained whole-outline/tint; existing plan/build label carries meaning. |
| `.builder-result` | 2 status | Replace semantic rail variants with semantic whole-border/tint variants and existing result labels. |
| `.builder-changes li` | 5 hierarchy/structure | Retain intentionally as a nested change-list guide, not a card edge. |
| `.c2-item.at-risk > .c2-row::before` | 2 urgency | Remove the short red edge marker because the row already has explicit at-risk icon/text. |
| `.acct-list-row.selected` | 3 selection/focus | Migrate selection from a left edge to full border/surface treatment. |
| `.tax-contrib-panel`, `.imp-after-rec.new` | 5 hierarchy/structure | Retain neutral inset dividers; they express nesting and are not semantic color rails. |
| Chat/skill blockquotes | 5 hierarchy/structure | Retain conventional neutral quote guides. |
| Account drawer edge, table/segmented dividers, chart event/dry lines, funding segments, popover arrows, resize/navigation rails | 5 hierarchy/structure | Exclude and retain; these are physical boundaries or data marks, not card decoration. |

The visual reference and state matrix are in the commissioned [Open Design artifact](http://127.0.0.1:49160/api/projects/bef4088d-e925-4b69-9b5e-f3fb48b110ce/raw/Card%20Edge%20Accent%20Audit.html).

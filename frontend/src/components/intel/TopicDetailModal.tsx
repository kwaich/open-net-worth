import { useState } from 'react'
import {
  Modal,
  Button,
  Switch,
  TextField,
  TextArea,
  FormField,
  TagInput,
  CollapsibleSection,
} from '../ui'
import { authFetch } from '../../api/client'
import { LuciusDraftLink } from './LuciusAssist'
import { luciusDraftTopicInstruction } from '../../utils/luciusEngine'
import { advancedSourcesBadge, CustomFeedsField } from './NewsSourcesFields'
import { errorDetail, watchlistFieldError } from './WatchlistsSection'
import type { WatchlistItem } from './WatchlistsSection'

interface Props {
  item: WatchlistItem
  onClose: () => void
  onSaved: () => void
  onDelete: () => void
}

const MAX_LINKED_TICKERS = 10

export function TopicDetailModal({ item, onClose, onSaved, onDelete }: Props) {
  const [label, setLabel] = useState(item.label || item.value)
  const [note, setNote] = useState(item.note || '')
  const [muted, setMuted] = useState(item.monitor_news === false)
  const [linkedTickers, setLinkedTickers] = useState<string[]>(item.linked_tickers || [])
  const [feeds, setFeeds] = useState<string[]>(item.custom_feed_urls || [])
  const [advOpen, setAdvOpen] = useState(false)
  const [linkedTickersError, setLinkedTickersError] = useState<string | null>(null)
  const [feedsError, setFeedsError] = useState<string | null>(null)
  const [invalidFeedUrl, setInvalidFeedUrl] = useState<string | null>(null)
  const [genericError, setGenericError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  async function save() {
    setSaving(true)
    setLinkedTickersError(null)
    setFeedsError(null)
    setInvalidFeedUrl(null)
    setGenericError(null)
    try {
      const res = await authFetch(`/api/watchlist/items/${item.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          label: label.trim() || item.label,
          note: note.trim(),
          monitor_news: !muted,
          linked_tickers: linkedTickers,
          custom_feed_urls: feeds.map((u) => u.trim()).filter(Boolean),
        }),
      })
      if (!res.ok) {
        if (res.status === 422) {
          const detail = await errorDetail(res, 'Could not save — please try again.')
          const { field, quoted } = watchlistFieldError(detail)
          if (field) {
            setAdvOpen(true)
            if (field === 'linked_tickers') setLinkedTickersError(detail)
            else {
              setFeedsError(detail)
              if (quoted) setInvalidFeedUrl(quoted)
            }
            return
          }
        }
        setGenericError('Could not save — please try again.')
        return
      }
      onSaved()
      onClose()
    } catch {
      setGenericError('Could not save — please try again.')
    } finally {
      setSaving(false)
    }
  }

  const badge = advancedSourcesBadge(linkedTickers.length, feeds.filter((u) => u.trim()).length)

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={
        <span className="intel-td-title">
          <span className="intel-wlc-topic-tag">topic</span>
          {item.added_by === 'ai' && (
            <span className="intel-wlc-row-badge intel-wlc-row-badge--ai">AI</span>
          )}
        </span>
      }
      footer={
        <div className="intel-pa-footer">
          <Button variant="ghost" className="intel-pa-remove" onClick={onDelete}>
            Delete topic
          </Button>
          {genericError && <div className="intel-wlc-add-err">{genericError}</div>}
          <div className="intel-pa-footer-right">
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void save()} loading={saving}>
              Save
            </Button>
          </div>
        </div>
      }
    >
      <p className="intel-pa-intro">
        Lucius scans news for this and flags matches in your brief &amp; notifications.
      </p>

      <div className="intel-pa-mute">
        <div className="intel-pa-mute-body">
          <div className={`intel-pa-mute-title${muted ? ' intel-pa-mute-title--muted' : ''}`}>
            {muted ? 'Muted' : 'Scanning on'}
          </div>
          <div className="intel-pa-mute-sub">
            {muted
              ? "Paused — Lucius won't flag matches for this topic."
              : 'Mute to stop flagging this topic without deleting it.'}
          </div>
        </div>
        <Switch checked={muted} onChange={(e) => setMuted(e.target.checked)} label="Mute topic" />
      </div>

      <div className="intel-td-field">
        <div className="intel-pa-instr-label">Topic name</div>
        <TextField
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Topic name"
        />
      </div>

      <div className="intel-td-field">
        <div className="intel-pa-instr-head">
          <span className="intel-pa-instr-label">Instructions for Lucius</span>
          <LuciusDraftLink
            label={note.trim() ? 'Rewrite' : 'Draft from topic name'}
            onDraft={() => setNote(luciusDraftTopicInstruction(label))}
          />
        </div>
        <TextArea
          rows={5}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="What should Lucius watch for and flag? e.g. escalation or ceasefire; gold safe-haven bid; oil spikes; risk-off equity moves — and what it means for my book."
        />
      </div>

      <div className="intel-td-field">
        <CollapsibleSection
          title="Advanced: news sources"
          defaultOpen={false}
          open={advOpen}
          onOpenChange={setAdvOpen}
          actions={badge ? <span className="intel-td-adv-count">{badge}</span> : undefined}
        >
          <FormField
            label="Linked tickers"
            htmlFor="topic-linked-tickers"
            error={linkedTickersError}
            help={
              linkedTickersError
                ? undefined
                : linkedTickers.length >= MAX_LINKED_TICKERS
                  ? 'Maximum 10 tickers.'
                  : "Get this topic's news from these tickers' fast feeds — minutes instead of hours. Up to 10."
            }
          >
            <TagInput
              id="topic-linked-tickers"
              value={linkedTickers}
              onChange={setLinkedTickers}
              max={MAX_LINKED_TICKERS}
              placeholder="e.g. AAPL"
            />
          </FormField>

          <CustomFeedsField
            urls={feeds}
            onChange={setFeeds}
            error={feedsError}
            invalidUrl={invalidFeedUrl}
          />
        </CollapsibleSection>
      </div>
    </Modal>
  )
}

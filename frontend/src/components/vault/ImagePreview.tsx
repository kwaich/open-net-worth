import { useState } from 'react'
import type { VaultItem } from '../../api/vault'
import { Modal } from '../ui/Modal'
import { ZoomImage } from '../ui/ZoomImage'

/** A photo, and a tap opens it large IN the app. Never a link to the raw file: an installed
 *  app on a phone opens that link in its own window, with no way back (owner report). */
export function ImagePreview({ item, src }: { item: VaultItem; src: string }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        type="button"
        className="vault-preview vault-preview--image"
        aria-label={`Open ${item.title}`}
        onClick={() => setOpen(true)}
      >
        <img className="vault-preview-img" src={src} alt={item.title} />
      </button>
      {open && (
        <Modal
          open
          onClose={() => setOpen(false)}
          title={item.title}
          size="xl"
          className="vault-image-modal"
        >
          <div className="vault-modal-content" tabIndex={-1} data-autofocus>
            <ZoomImage className="vault-image-full" src={src} alt={item.title} />
          </div>
        </Modal>
      )}
    </>
  )
}

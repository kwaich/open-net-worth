import { useLayoutEffect, useState } from 'react'

/** The element's content-box size, kept current with a ResizeObserver. 0 until measured (and
 *  always 0 under jsdom), so callers supply their own fallback.
 *
 *  Returns a CALLBACK ref, not a ref object: an element rendered through a portal (Radix
 *  Dialog content) attaches AFTER its parent's layout effect, so a `useRef` + mount-effect pair
 *  never sees the node. */
export function useElementSize<T extends HTMLElement>(): [
  (node: T | null) => void,
  { w: number; h: number },
] {
  const [node, setNode] = useState<T | null>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  useLayoutEffect(() => {
    if (!node) return
    const read = () =>
      setSize((prev) =>
        prev.w === node.clientWidth && prev.h === node.clientHeight
          ? prev
          : { w: node.clientWidth, h: node.clientHeight },
      )
    read()
    const ro = new ResizeObserver(read)
    ro.observe(node)
    return () => ro.disconnect()
  }, [node])
  return [setNode, size]
}

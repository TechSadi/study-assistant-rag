import { useEffect, useRef, useState } from 'react'

// A button-toggled menu: closes on Escape (returning focus to the button)
// or on a press outside the button and its panel.
export default function useMenu() {
  const [open, setOpen] = useState(false)
  const button = useRef(null)
  const panel = useRef(null)

  useEffect(() => {
    if (!open) return
    function onPointerDown(e) {
      if (!button.current?.contains(e.target) && !panel.current?.contains(e.target)) setOpen(false)
    }
    function onKeyDown(e) {
      if (e.key !== 'Escape') return
      setOpen(false)
      button.current?.focus()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  function close() {
    setOpen(false)
    button.current?.focus()
  }

  return { open, setOpen, close, button, panel }
}

"use client";
import { useEffect, useId, useRef, useState } from "react";

const EDGE_PAD = 12;
const POP_HALF_WIDTH = 140; // half of .pop's max-width, keeps it off-screen edges

/**
 * A small accessible "?" help tooltip. Hover or keyboard-focus to reveal.
 * The popover is `position: fixed` and placed from the trigger button's own
 * getBoundingClientRect() rather than `position: absolute` inside the
 * document flow — so it's never clipped by an ancestor's `overflow-x: auto`
 * (e.g. a scrolling table wrapper), regardless of where the trigger sits.
 */
export default function Tip({ text }: { text: string }) {
  const [pos, setPos] = useState<{ x: number; y: number; below: boolean } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const popId = useId();
  const open = pos !== null;

  function show() {
    const r = btnRef.current?.getBoundingClientRect();
    if (!r) return;
    const below = r.top < 90; // not enough room above — flip below the trigger
    const x = Math.min(
      Math.max(r.left + r.width / 2, POP_HALF_WIDTH + EDGE_PAD),
      window.innerWidth - POP_HALF_WIDTH - EDGE_PAD
    );
    const y = below ? r.bottom + 8 : r.top - 8;
    setPos({ x, y, below });
  }
  function hide() {
    setPos(null);
  }

  // Popover is position:fixed from the trigger's rect, computed once on
  // hover — re-run (or close) while scrolling/resizing so it doesn't detach
  // from the "?" button on long/scrollable views.
  useEffect(() => {
    if (!open) return;
    const update = () => show();
    window.addEventListener("scroll", update, { passive: true, capture: true });
    window.addEventListener("resize", update);
    return () => {
      window.removeEventListener("scroll", update, true);
      window.removeEventListener("resize", update);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return (
    <span className="tip">
      <button
        ref={btnRef}
        type="button"
        className="q"
        aria-label={text}
        aria-describedby={pos ? popId : undefined}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
      >
        ?
      </button>
      {pos && (
        <span
          id={popId}
          role="tooltip"
          className={`pop${pos.below ? " pop-below" : ""}`}
          style={{ left: pos.x, top: pos.y }}
        >
          {text}
        </span>
      )}
    </span>
  );
}

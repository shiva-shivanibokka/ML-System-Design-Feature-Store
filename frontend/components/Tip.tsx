"use client";
import { useId, useRef, useState } from "react";

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

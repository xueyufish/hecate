// Incremental server-sent-events frame parser.
//
// One instance consumes the decoded text of one HTTP stream. ``push``
// returns only frames that a blank line has fully terminated — an
// unterminated tail never produces a frame, so a connection that dies
// mid-frame cannot be mistaken for a delivered event (the cursor must not
// advance over data the server never finished sending). Line endings
// follow the SSE grammar: ``\n``, ``\r\n`` or a lone ``\r``. ``data``
// lines accumulate with embedded newlines; comments (``:``) and unknown
// fields are ignored per the grammar. A frame that carries neither data
// nor an ``event`` field is not dispatched.

export interface SseFrame {
  /** ``event:`` field value, or ``null`` for the default event type. */
  event: string | null;
  /** Joined ``data:`` lines (``\n`` between lines); empty when absent. */
  data: string;
}

export class SseFrameParser {
  private buffer = "";

  /** Feed one decoded chunk; return every frame it completes. */
  push(chunk: string): SseFrame[] {
    this.buffer += chunk;
    const frames: SseFrame[] = [];
    for (;;) {
      const end = findFrameEnd(this.buffer);
      if (end === null) break;
      const frame = takeFrame(this.buffer.slice(0, end.frameBodyLength));
      this.buffer = this.buffer.slice(end.nextOffset);
      if (frame !== null) frames.push(frame);
    }
    return frames;
  }

  /**
   * End of stream: report residual content.
   *
   * Returns the frame the residual lines would have formed — with
   * ``terminated: false`` — so the caller can distinguish "connection
   * closed mid-frame" from a clean end, and never advances a resume
   * cursor over it. ``null`` when the stream ended on a frame boundary.
   */
  finish(): { frame: SseFrame; terminated: false } | null {
    if (this.buffer === "") return null;
    const frame = takeFrame(this.buffer);
    this.buffer = "";
    if (frame === null) return null;
    return { frame, terminated: false };
  }
}

/** A frame ends at the first empty line; return its extent, or null. */
function findFrameEnd(buffer: string): { frameBodyLength: number; nextOffset: number } | null {
  let pos = 0;
  while (pos < buffer.length) {
    const breakAt = nextBreak(buffer, pos);
    if (breakAt === -1) return null;
    const length = buffer[breakAt] === "\r" && buffer[breakAt + 1] === "\n" ? 2 : 1;
    if (breakAt === pos) {
      // The line starting at ``pos`` is empty: everything before it is
      // one frame body; parsing continues after this terminator.
      return { frameBodyLength: pos, nextOffset: breakAt + length };
    }
    pos = breakAt + length;
  }
  return null;
}

function nextBreak(buffer: string, from: number): number {
  const nl = buffer.indexOf("\n", from);
  const cr = buffer.indexOf("\r", from);
  if (nl === -1) return cr;
  if (cr === -1) return nl;
  return Math.min(nl, cr);
}

function takeFrame(body: string): SseFrame | null {
  let event: string | null = null;
  const dataLines: string[] = [];
  let sawField = false;
  for (const rawLine of body.split(/\r\n|\r|\n/)) {
    if (rawLine === "" || rawLine.startsWith(":")) continue;
    sawField = true;
    const colon = rawLine.indexOf(":");
    const name = colon === -1 ? rawLine : rawLine.slice(0, colon);
    // The grammar strips exactly one optional space after the colon.
    let value = colon === -1 ? "" : rawLine.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (name === "data") dataLines.push(value);
    else if (name === "event") event = value;
  }
  if (!sawField || (dataLines.length === 0 && event === null)) return null;
  return { event, data: dataLines.join("\n") };
}

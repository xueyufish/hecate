"use client";

import { useState } from "react";

interface ChannelSelectorProps {
  available: string[];
  readable: string[];
  writable: string[];
  onChange: (channels: { readable: string[]; writable: string[] }) => void;
}

export function ChannelSelector({
  available,
  readable,
  writable,
  onChange,
}: ChannelSelectorProps) {
  const [newChannel, setNewChannel] = useState("");

  function handleToggle(channel: string, list: "readable" | "writable") {
    const current = list === "readable" ? readable : writable;
    const updated = current.includes(channel)
      ? current.filter((c) => c !== channel)
      : [...current, channel];
    onChange(
      list === "readable"
        ? { readable: updated, writable }
        : { readable, writable: updated }
    );
  }

  function handleAddChannel() {
    const name = newChannel.trim();
    if (!name) return;
    onChange({ readable: [...readable, name], writable });
    setNewChannel("");
  }

  const allChannels =
    available.length > 0
      ? available
      : Array.from(new Set([...readable, ...writable]));

  return (
    <div className="space-y-2">
      <div>
        <label className="mb-1 block text-xs font-medium text-muted-foreground">
          Channels
        </label>
        {allChannels.length === 0 && (
          <p className="mb-1 text-[10px] text-muted-foreground">
            Add channels in graph settings
          </p>
        )}
        <div className="space-y-0.5">
          {allChannels.map((ch) => (
            <div
              key={ch}
              className="flex items-center gap-2 rounded border px-2 py-1 text-xs"
            >
              <span className="flex-1 truncate">{ch}</span>
              <label className="flex items-center gap-1 text-[10px]">
                <input
                  type="checkbox"
                  checked={readable.includes(ch)}
                  onChange={() => handleToggle(ch, "readable")}
                  className="h-3 w-3"
                />
                R
              </label>
              <label className="flex items-center gap-1 text-[10px]">
                <input
                  type="checkbox"
                  checked={writable.includes(ch)}
                  onChange={() => handleToggle(ch, "writable")}
                  className="h-3 w-3"
                />
                W
              </label>
            </div>
          ))}
        </div>
      </div>
      {available.length === 0 && (
        <div className="flex gap-1">
          <input
            type="text"
            className="flex-1 rounded-md border px-2 py-1 text-xs"
            value={newChannel}
            onChange={(e) => setNewChannel(e.target.value)}
            placeholder="Add channel..."
            onKeyDown={(e) => {
              if (e.key === "Enter") handleAddChannel();
            }}
          />
          <button
            type="button"
            onClick={handleAddChannel}
            className="rounded-md border px-2 py-1 text-xs hover:bg-muted"
          >
            +
          </button>
        </div>
      )}
    </div>
  );
}

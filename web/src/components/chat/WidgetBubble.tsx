"use client";

import { useState, type ReactNode } from "react";
import { MessageCircle, X } from "lucide-react";

import styles from "@/app/embed/chat/embed.module.css";

export interface WidgetBubbleProps {
  agentId: string;
  conversationId: string | null;
  surface: ReactNode;
}

export function WidgetBubble({ surface }: WidgetBubbleProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <div className={styles.shell} data-testid="widget-bubble-shell">
      {!isExpanded && (
        <button
          type="button"
          className={styles.bubble}
          onClick={() => setIsExpanded(true)}
          aria-label="Open chat"
          data-testid="widget-bubble-button"
        >
          <MessageCircle size={20} aria-hidden="true" />
        </button>
      )}

      {isExpanded && (
        <div
          className={styles.window}
          role="dialog"
          aria-label="Chat"
          data-testid="widget-bubble-window"
        >
          <header>
            <div className={styles.windowHeaderTitle}>Chat</div>
            <button
              type="button"
              className={styles.close}
              onClick={() => setIsExpanded(false)}
              aria-label="Close chat"
              data-testid="widget-bubble-close"
            >
              <X size={16} aria-hidden="true" />
            </button>
          </header>
          <div className={styles.surface}>{surface}</div>
        </div>
      )}
    </div>
  );
}

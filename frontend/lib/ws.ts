"use client";

import { useEffect, useRef, useState } from "react";

export type WsMessage = {
  type: string;
  timestamp: string;
  snapshot?: any;
  top_signals?: any[];
  pcr?: any;
  fii_dii?: any;
};

export function useWebSocket() {
  const [lastMessage, setLastMessage] = useState<WsMessage | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const connect = () => {
      const ws = new WebSocket("ws://localhost:8000/ws");
      wsRef.current = ws;

      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        setTimeout(connect, 3000);
      };
      ws.onmessage = (e) => {
        try {
          setLastMessage(JSON.parse(e.data));
        } catch {}
      };
    };

    connect();
    const ping = setInterval(() => wsRef.current?.send("ping"), 30_000);

    return () => {
      clearInterval(ping);
      wsRef.current?.close();
    };
  }, []);

  return { lastMessage, connected };
}

import { useEffect, useRef } from "react";
import { useStore } from "../store/useStore";

const RECONNECT_INTERVAL = 3000;

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleEvent = useStore((s) => s.handleEvent);
  const setWsStatus = useStore((s) => s.setWsStatus);
  const pushLog = useStore((s) => s.pushLog);

  useEffect(() => {
    let mounted = true;

    const connect = () => {
      if (!mounted) return;
      if (wsRef.current && wsRef.current.readyState <= 1) return;

      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${proto}//${location.host}/ws`;
      setWsStatus("connecting");

      try {
        const ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => {
          setWsStatus("online");
          pushLog("WebSocket 已连接", "ok");
          if (reconnectTimer.current) {
            clearTimeout(reconnectTimer.current);
            reconnectTimer.current = null;
          }
          ws.send(JSON.stringify({ type: "sync_request" }));
        };

        ws.onmessage = (ev) => {
          try {
            const evt = JSON.parse(ev.data);
            handleEvent(evt);
          } catch { /* ignore */ }
        };

        ws.onerror = () => setWsStatus("error");

        ws.onclose = () => {
          setWsStatus("offline");
          wsRef.current = null;
          if (mounted) scheduleReconnect();
        };
      } catch {
        setWsStatus("error");
        scheduleReconnect();
      }
    };

    const scheduleReconnect = () => {
      if (reconnectTimer.current) return;
      reconnectTimer.current = setTimeout(() => {
        reconnectTimer.current = null;
        connect();
      }, RECONNECT_INTERVAL);
    };

    connect();

    return () => {
      mounted = false;
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [handleEvent, setWsStatus, pushLog]);
}

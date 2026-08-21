import { useEffect, useRef } from "react";
import { useStore } from "../store/useStore";

const RECONNECT_INTERVAL = 3000;

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleEvent = useStore((s) => s.handleEvent);
  const setWsStatus = useStore((s) => s.setWsStatus);
  const pushLog = useStore((s) => s.pushLog);
  const authState = useStore((s) => s.authState);

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
          // 仅在已登录时重连; 未登录时 WS 会被服务端 4401 拒绝, 避免空转重连
          if (mounted && useStore.getState().authState === "authenticated") {
            scheduleReconnect();
          }
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

    const cleanup = () => {
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

    // 仅在已登录时建立 WS 连接; 未登录/检查中不连接, 避免被 4401 拒绝后空转重连
    if (authState === "authenticated") {
      connect();
    } else {
      setWsStatus("offline");
    }

    return cleanup;
  }, [handleEvent, setWsStatus, pushLog, authState]);
}

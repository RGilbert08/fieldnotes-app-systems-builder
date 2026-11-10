import { useEffect, useRef, useState } from "react";
import { toApiUrl } from "../config";

export function canvasLayoutStreamUrl(dashboardViewport) {
  const url = new URL(
    toApiUrl("/api/ws/layout-stream"),
    window.location.origin,
  );
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("dashboard_viewport", dashboardViewport);
  return url.toString();
}

export function useCanvasLayoutStream({ dashboardViewport, onLayout, onEvent }) {
  const onLayoutRef = useRef(onLayout);
  const onEventRef = useRef(onEvent);
  const [connectionState, setConnectionState] = useState("connecting");

  useEffect(() => {
    onLayoutRef.current = onLayout;
  }, [onLayout]);

  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    if (!dashboardViewport || typeof WebSocket === "undefined") {
      setConnectionState("unavailable");
      return undefined;
    }

    let active = true;
    let socket = null;
    let reconnectTimer = null;
    let heartbeatTimer = null;
    let reconnectDelay = 500;

    function connect() {
      if (!active) return;
      setConnectionState("connecting");
      try {
        socket = new WebSocket(canvasLayoutStreamUrl(dashboardViewport));
      } catch {
        setConnectionState("reconnecting");
        scheduleReconnect();
        return;
      }

      socket.onopen = () => {
        if (!active) return;
        reconnectDelay = 500;
        setConnectionState("connected");
        heartbeatTimer = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: "ping" }));
          }
        }, 20_000);
      };
      socket.onmessage = (event) => {
        if (!active) return;
        let payload;
        try {
          payload = JSON.parse(event.data);
        } catch {
          return;
        }
        onEventRef.current?.(payload);
        if (
          payload?.type === "layout.updated" &&
          payload.dashboard_viewport === dashboardViewport &&
          payload.layout &&
          typeof payload.layout === "object"
        ) {
          onLayoutRef.current?.(payload);
        }
        if (payload?.type === "classroom.otp_gate.updated") {
          window.dispatchEvent(new CustomEvent("fieldnotes:classroom-otp-gate-updated", {
            detail: { expiresAt: payload.expiresAt || payload.expires_at || null },
          }));
        }
        if (payload?.type === "lesson.progress.refresh") {
          window.dispatchEvent(new CustomEvent("fieldnotes:lesson-progress-refresh", {
            detail: { reason: payload.reason || "server_refresh" },
          }));
        }
      };
      socket.onerror = () => {
        setConnectionState("reconnecting");
        socket?.close();
      };
      socket.onclose = () => {
        if (heartbeatTimer !== null) {
          window.clearInterval(heartbeatTimer);
          heartbeatTimer = null;
        }
        if (active) {
          setConnectionState("reconnecting");
          scheduleReconnect();
        }
      };
    }

    function scheduleReconnect() {
      if (!active || reconnectTimer !== null) return;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        reconnectDelay = Math.min(reconnectDelay * 2, 10_000);
        connect();
      }, reconnectDelay);
    }

    connect();
    return () => {
      active = false;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (heartbeatTimer !== null) window.clearInterval(heartbeatTimer);
      socket?.close();
    };
  }, [dashboardViewport]);

  return connectionState;
}
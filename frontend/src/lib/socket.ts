import { io, Socket } from "socket.io-client";

const defaultUrl = import.meta.env.DEV ? "http://localhost:8000" : "";
/** Single backend URL for both Socket.IO and REST API (VITE_SOCKET_URL or VITE_BACKEND_URL). */
export const backendUrl =
    (import.meta.env.VITE_BACKEND_URL as string | undefined)?.trim() ||
    (import.meta.env.VITE_SOCKET_URL as string | undefined)?.trim() ||
    defaultUrl;
const apiKey = (import.meta.env.VITE_SOCKET_API_KEY as string | undefined)?.trim() || undefined;

export const socket: Socket = io(backendUrl, {
    autoConnect: false,
    transports: ["websocket"],
    ...(apiKey && { auth: { api_key: apiKey } }),
});

/** Base URL for REST API; use with apiUrl("/api/..."). */
export function apiUrl(path: string): string {
    return backendUrl ? `${backendUrl.replace(/\/$/, "")}${path.startsWith("/") ? path : `/${path}`}` : path;
}

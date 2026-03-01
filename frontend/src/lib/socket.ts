import { io, Socket } from "socket.io-client";

const defaultUrl = import.meta.env.DEV ? "http://localhost:8000" : "";
const socketUrl = (import.meta.env.VITE_SOCKET_URL as string | undefined)?.trim() || defaultUrl;
const apiKey = (import.meta.env.VITE_SOCKET_API_KEY as string | undefined)?.trim() || undefined;
export const socket: Socket = io(socketUrl, {
    autoConnect: false,
    transports: ["websocket"],
    ...(apiKey && { auth: { api_key: apiKey } }),
});

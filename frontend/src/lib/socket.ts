import { io, Socket } from "socket.io-client";

const URL = import.meta.env.DEV ? "http://localhost:8000" : "";
const apiKey = import.meta.env.VITE_SOCKET_API_KEY as string | undefined;
export const socket: Socket = io(URL, {
    autoConnect: false,
    transports: ["websocket"],
    ...(apiKey && { auth: { api_key: apiKey } }),
});

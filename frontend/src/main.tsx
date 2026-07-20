import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";
import App from "./App";
import { SessionPlaybackPage } from "./pages/SessionPlaybackPage";
import { AuthGate } from "./components/AuthGate";
import { TooltipProvider } from "@/components/ui/tooltip";

// AuthGate stays outside the router so every route inherits the password gate.
// App is mounted only on "/" — it renders SessionSetup, which rewrites the URL via
// history.replaceState and would clobber the /sessions/:id route if left mounted.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TooltipProvider>
      <AuthGate>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<App />} />
            <Route path="/sessions/:sessionId" element={<SessionPlaybackPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </AuthGate>
    </TooltipProvider>
  </StrictMode>
);

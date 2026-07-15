import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { apiUrl } from "../lib/socket";
import { Input } from "@/components/ui/input";

// Session token from a successful login. Stored in sessionStorage so the user stays
// unlocked for the browser tab session and is re-prompted when the tab is reopened.
const TOKEN_KEY = "app_auth_token";

type GateStatus = "checking" | "locked" | "unlocked";

/**
 * Password gate around the app. The password is validated server-side (POST /api/login)
 * so it never ships in the bundle. If the backend reports no password is required
 * (APP_PASSWORD unset), the gate is transparent.
 */
export function AuthGate({ children }: { children: ReactNode }) {
    const [status, setStatus] = useState<GateStatus>(() =>
        sessionStorage.getItem(TOKEN_KEY) ? "unlocked" : "checking"
    );
    const [password, setPassword] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [submitting, setSubmitting] = useState(false);

    // Ask the backend whether a password is required (unless already unlocked this session).
    useEffect(() => {
        if (status !== "checking") return;
        let cancelled = false;
        (async () => {
            try {
                const res = await fetch(apiUrl("/api/auth-config"));
                const data = await res.json();
                if (!cancelled) setStatus(data.password_required ? "locked" : "unlocked");
            } catch {
                // Backend unreachable — show the gate rather than leaving the app open.
                if (!cancelled) setStatus("locked");
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [status]);

    const onSubmit = async (e: FormEvent) => {
        e.preventDefault();
        if (submitting) return;
        setSubmitting(true);
        setError(null);
        try {
            const res = await fetch(apiUrl("/api/login"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ password }),
            });
            if (res.ok) {
                const data = await res.json();
                sessionStorage.setItem(TOKEN_KEY, data.token);
                setStatus("unlocked");
            } else {
                setError("كلمة المرور غير صحيحة");
            }
        } catch {
            setError("تعذّر الاتصال بالخادم");
        } finally {
            setSubmitting(false);
        }
    };

    if (status === "unlocked") return <>{children}</>;

    if (status === "checking") {
        return (
            <div className="flex min-h-screen items-center justify-center">
                <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-primary" />
            </div>
        );
    }

    return (
        <div className="flex min-h-screen items-center justify-center px-4">
            <form
                onSubmit={onSubmit}
                className="w-full max-w-sm rounded-2xl border border-border/60 bg-surface-elevated/80 p-8 shadow-xl backdrop-blur-md"
            >
                <p className="mb-6 text-center font-arabic text-sm text-text-secondary">
                    الرجاء إدخال كلمة المرور للمتابعة
                </p>

                <Input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="كلمة المرور"
                    autoFocus
                    className="text-center font-arabic"
                    aria-invalid={error ? true : undefined}
                    disabled={submitting}
                />

                {error && (
                    <p className="mt-3 text-center font-arabic text-sm text-error">{error}</p>
                )}

                <button
                    type="submit"
                    disabled={submitting || !password}
                    className="mt-6 w-full rounded-md bg-primary py-2.5 font-arabic font-medium text-white transition-colors hover:bg-primary-dark disabled:cursor-not-allowed disabled:opacity-50"
                >
                    {submitting ? "جارٍ التحقق..." : "دخول"}
                </button>
            </form>
        </div>
    );
}

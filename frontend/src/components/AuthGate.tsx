import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { AlertCircle, BookOpen, Eye, EyeOff, Loader2, LockKeyhole } from "lucide-react";
import { apiUrl } from "../lib/socket";

// Session token from a successful login. Stored in sessionStorage so the user stays
// unlocked for the browser tab session and is re-prompted when the tab is reopened.
const TOKEN_KEY = "app_auth_token";

type GateStatus = "checking" | "locked" | "unlocked";

/**
 * Ambient backdrop shared by the loading and the locked states, so the gate doesn't
 * visually jump when the auth-config request resolves.
 */
function GateBackdrop() {
    return (
        <div aria-hidden className="pointer-events-none fixed inset-0 overflow-hidden">
            {/* Gold glow, top-start */}
            <div className="absolute -top-40 start-[-10rem] h-[32rem] w-[32rem] rounded-full bg-gold/12 blur-[120px]" />
            {/* Softer gold glow, bottom-end */}
            <div className="absolute -bottom-48 end-[-8rem] h-[30rem] w-[30rem] rounded-full bg-gold-light/8 blur-[120px]" />
            {/* Fine grid, fades out towards the edges */}
            <div
                className="absolute inset-0 opacity-[0.35]"
                style={{
                    backgroundImage:
                        "linear-gradient(to right, rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,0.03) 1px, transparent 1px)",
                    backgroundSize: "56px 56px",
                    maskImage: "radial-gradient(ellipse 70% 60% at 50% 40%, #000 30%, transparent 100%)",
                    WebkitMaskImage:
                        "radial-gradient(ellipse 70% 60% at 50% 40%, #000 30%, transparent 100%)",
                }}
            />
        </div>
    );
}

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
    const [showPassword, setShowPassword] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [submitting, setSubmitting] = useState(false);
    const inputRef = useRef<HTMLInputElement>(null);

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
        if (submitting || !password) return;
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
                setPassword("");
                inputRef.current?.focus();
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
            <div className="relative flex min-h-screen items-center justify-center px-4">
                <GateBackdrop />
                <div
                    role="status"
                    aria-live="polite"
                    className="relative flex flex-col items-center gap-6"
                >
                    <div className="relative flex h-20 w-20 items-center justify-center">
                        {/* Static track + spinning arc */}
                        <span className="absolute inset-0 rounded-full border-2 border-white/[0.07]" />
                        <span className="absolute inset-0 animate-spin rounded-full border-2 border-transparent border-t-gold [animation-duration:1.1s]" />
                        <span className="absolute inset-2 rounded-full bg-gold/10 blur-md" />
                        <BookOpen className="relative h-7 w-7 text-gold" strokeWidth={1.5} />
                    </div>

                    <div className="text-center">
                        <p className="font-arabic text-base font-medium text-text-primary">
                            جاري تحميل الموديل
                        </p>
                        <p className="mt-1.5 font-arabic text-sm text-text-muted">
                            قد يستغرق ذلك بضع لحظات عند أول تشغيل
                        </p>
                    </div>

                    {/* Indeterminate progress bar */}
                    <div className="h-1 w-48 overflow-hidden rounded-full bg-white/[0.06]">
                        <div className="auth-progress h-full w-1/3 rounded-full bg-gradient-to-r from-gold/0 via-gold to-gold/0" />
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="relative flex min-h-screen items-center justify-center px-4 py-10">
            <GateBackdrop />

            <div className="relative w-full max-w-[26rem]">
                {/* Brand */}
                <div className="mb-8 flex flex-col items-center text-center">
                    <div className="relative mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-gold/20 bg-gradient-to-br from-gold/25 to-gold/5 shadow-lg shadow-black/40">
                        <span className="absolute inset-0 rounded-2xl bg-gold/10 blur-lg" />
                        <BookOpen className="relative h-7 w-7 text-gold-light" strokeWidth={1.5} />
                    </div>
                    <h1 className="font-arabic text-2xl font-bold tracking-tight text-text-primary">
                        منصة التلاوة
                    </h1>
                    <p className="mt-2 font-arabic text-sm text-text-secondary">
                        التعرّف على التلاوة وتصحيحها كلمةً بكلمة
                    </p>
                </div>

                {/* Card */}
                <form
                    onSubmit={onSubmit}
                    noValidate
                    className="rounded-3xl border border-white/[0.08] bg-white/[0.035] p-7 shadow-2xl shadow-black/50 backdrop-blur-xl sm:p-8"
                >
                    <div className="mb-6 flex items-center gap-2.5">
                        <LockKeyhole className="h-4 w-4 shrink-0 text-text-muted" strokeWidth={1.75} />
                        <p className="font-arabic text-sm text-text-secondary">
                            هذه المنصة محمية بكلمة مرور
                        </p>
                    </div>

                    <label
                        htmlFor="app-password"
                        className="mb-2 block font-arabic text-xs font-medium text-text-secondary"
                    >
                        كلمة المرور
                    </label>

                    <div className="relative">
                        <input
                            id="app-password"
                            ref={inputRef}
                            type={showPassword ? "text" : "password"}
                            value={password}
                            onChange={(e) => {
                                setPassword(e.target.value);
                                if (error) setError(null);
                            }}
                            placeholder="••••••••"
                            autoFocus
                            autoComplete="current-password"
                            disabled={submitting}
                            aria-invalid={error ? true : undefined}
                            aria-describedby={error ? "app-password-error" : undefined}
                            className={`h-12 w-full rounded-xl border bg-black/25 pe-12 ps-4 font-arabic text-[0.95rem] text-text-primary outline-none transition-all placeholder:text-text-muted/60 disabled:cursor-not-allowed disabled:opacity-60 ${
                                error
                                    ? "border-error/60 focus:border-error focus:ring-2 focus:ring-error/25"
                                    : "border-white/10 hover:border-white/20 focus:border-gold/70 focus:ring-2 focus:ring-gold/25"
                            }`}
                        />

                        <button
                            type="button"
                            onClick={() => setShowPassword((v) => !v)}
                            tabIndex={-1}
                            aria-label={showPassword ? "إخفاء كلمة المرور" : "إظهار كلمة المرور"}
                            className="absolute inset-y-0 end-0 flex w-12 items-center justify-center rounded-e-xl text-text-muted transition-colors hover:text-gold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gold/40"
                        >
                            {showPassword ? (
                                <EyeOff className="h-[1.1rem] w-[1.1rem]" strokeWidth={1.75} />
                            ) : (
                                <Eye className="h-[1.1rem] w-[1.1rem]" strokeWidth={1.75} />
                            )}
                        </button>
                    </div>

                    {/* Reserved slot so the card doesn't jump when the error appears */}
                    <div className="min-h-[1.75rem] pt-2">
                        {error && (
                            <p
                                id="app-password-error"
                                role="alert"
                                className="auth-shake flex items-center gap-1.5 font-arabic text-sm text-error"
                            >
                                <AlertCircle className="h-4 w-4 shrink-0" strokeWidth={2} />
                                {error}
                            </p>
                        )}
                    </div>

                    <button
                        type="submit"
                        disabled={submitting || !password}
                        className="mt-3 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-b from-gold-light to-gold font-arabic text-[0.95rem] font-semibold text-[#1a1408] shadow-lg shadow-black/40 transition-all hover:from-gold-light hover:to-gold-light focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gold/60 focus-visible:ring-offset-2 focus-visible:ring-offset-surface active:scale-[0.99] disabled:cursor-not-allowed disabled:from-white/[0.06] disabled:to-white/[0.06] disabled:text-text-muted disabled:shadow-none"
                    >
                        {submitting ? (
                            <>
                                <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2} />
                                جارٍ التحقق…
                            </>
                        ) : (
                            "دخول"
                        )}
                    </button>
                </form>

                <p className="mt-6 text-center font-arabic text-xs text-text-muted">
                    تُحفظ الجلسة في هذا المتصفح حتى إغلاق التبويب
                </p>
            </div>
        </div>
    );
}

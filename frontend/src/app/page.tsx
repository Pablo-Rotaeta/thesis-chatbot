"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { startSession, sendMessage, endSession, SystemType, Provider } from "@/lib/api";

// ─── Types ───────────────────────────────────────────────────────────────────

interface Msg { role: "user" | "assistant"; content: string; ts: number; }
type Screen = "setup" | "chat" | "questionnaire" | "done";

// ─── Questionnaire ────────────────────────────────────────────────────────────

const UES_QUESTIONS = [
  { id: "a1", label: "Systemet utförde den avsedda uppgiften", type: "yn" },
  { id: "a2", label: "Hur nöjd är du med resultatet?", type: "scale" },
  { id: "b1", label: "Jag var engagerad i konversationen med systemet", type: "scale" },
  { id: "b2", label: "Systemet var enkelt att använda", type: "scale" },
  { id: "b3", label: "Interaktionen med systemet var belönande", type: "scale" },
  { id: "b4", label: "Systemets funktioner mötte mina behov", type: "scale" },
  { id: "b5", label: "Jag tyckte att systemet var lätt att kommunicera med", type: "scale" },
];

// ─── Component ────────────────────────────────────────────────────────────────

export default function Page() {
  const [screen, setScreen] = useState<Screen>("setup");
  const [systemType, setSystemType] = useState<SystemType>("skill_based");
  const [provider, setProvider] = useState<Provider>("gemini");
  const [model, setModel] = useState("gemini-2.0-flash-001");
  const [sessionId, setSessionId] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [isComplete, setIsComplete] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs, loading]);

  // ── Setup ────────────────────────────────────────────────────────────────

  async function handleStart() {
    setError("");
    setLoading(true);
    try {
      const res = await startSession(systemType, provider, model || undefined);
      setSessionId(res.session_id);
      setMsgs([{ role: "assistant", content: res.opening_message, ts: Date.now() }]);
      setScreen("chat");
      setTimeout(() => inputRef.current?.focus(), 100);
    } catch (e: any) {
      setError("Kunde inte starta sessionen. Kontrollera att backend är igång.");
    } finally {
      setLoading(false);
    }
  }

  // ── Chat ─────────────────────────────────────────────────────────────────

  const handleSend = useCallback(async () => {
    if (!input.trim() || loading || isComplete) return;
    const text = input.trim();
    setInput("");
    setMsgs(m => [...m, { role: "user", content: text, ts: Date.now() }]);
    setLoading(true);
    try {
      const res = await sendMessage(sessionId, text);
      setMsgs(m => [...m, { role: "assistant", content: res.reply, ts: Date.now() }]);
      if (res.is_complete) {
        setIsComplete(true);
        setTimeout(async () => {
          await endSession(sessionId, true);
          setScreen("questionnaire");
        }, 2000);
      }
    } catch {
      setMsgs(m => [...m, { role: "assistant", content: "⚠️ Ett fel uppstod. Försök igen.", ts: Date.now() }]);
    } finally {
      setLoading(false);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [input, loading, isComplete, sessionId]);

  async function handleFinish() {
    await endSession(sessionId, isComplete);
    setScreen("questionnaire");
  }

  // ── Questionnaire ─────────────────────────────────────────────────────────

  function handleAnswer(id: string, val: string) {
    setAnswers(a => ({ ...a, [id]: val }));
  }

  async function handleSubmitQuestionnaire() {
    // In production, POST answers to backend linked to session_id
    console.log("Questionnaire answers:", { session_id: sessionId, ...answers });
    setScreen("done");
  }

  // ── Render ────────────────────────────────────────────────────────────────

  if (screen === "setup") return <SetupScreen
    systemType={systemType} setSystemType={setSystemType}
    provider={provider} setProvider={setProvider}
    model={model} setModel={setModel}
    onStart={handleStart} loading={loading} error={error}
  />;

  if (screen === "chat") return <ChatScreen
    msgs={msgs} loading={loading} input={input}
    setInput={setInput} onSend={handleSend} onFinish={handleFinish}
    inputRef={inputRef} bottomRef={bottomRef}
    isComplete={isComplete} systemType={systemType} provider={provider}
  />;

  if (screen === "questionnaire") return <QuestionnaireScreen
    answers={answers} onAnswer={handleAnswer} onSubmit={handleSubmitQuestionnaire}
    sessionId={sessionId}
  />;

  return <DoneScreen />;
}

// ─── Setup Screen ─────────────────────────────────────────────────────────────

function SetupScreen({ systemType, setSystemType, provider, setProvider, model, setModel, onStart, loading, error }: any) {
  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "24px" }}>
      <div style={{ width: "100%", maxWidth: 460, background: "var(--surface)", borderRadius: "var(--radius)", border: "1px solid var(--border)", padding: "40px 36px", boxShadow: "var(--shadow)" }}>

        <div style={{ marginBottom: 32 }}>
          <div style={{ fontSize: 13, fontWeight: 500, color: "var(--accent)", letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 8 }}>
            Stockholms Bilverkstad
          </div>
          <h1 style={{ fontSize: 26, fontWeight: 600, lineHeight: 1.2, marginBottom: 8 }}>Boka din tid</h1>
          <p style={{ fontSize: 15, color: "var(--muted)", lineHeight: 1.6 }}>
            Välj systeminställningar nedan och starta sedan konversationen.
          </p>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

          <Field label="Systemtyp">
            <ToggleGroup
              options={[
                { value: "skill_based", label: "Skill-based", desc: "Styrd dialog" },
                { value: "unconstrained", label: "Fri LLM", desc: "Utan begränsning" },
              ]}
              value={systemType} onChange={setSystemType}
            />
          </Field>

          <Field label="AI-modell">
            <select value={provider} onChange={e => setProvider(e.target.value)}
              style={{ width: "100%", padding: "10px 14px", borderRadius: 10, border: "1px solid var(--border)", fontSize: 14, background: "var(--bg)", color: "var(--text)" }}>
              <option value="gemini">Google Gemini</option>
              <option value="openai">OpenAI GPT</option>
              <option value="anthropic">Anthropic Claude</option>
              <option value="ollama">Ollama (lokal)</option>
            </select>
          </Field>

          <Field label="Modellversion (valfritt)" hint="Lämna tomt för standard">
            <input value={model} onChange={e => setModel(e.target.value)}
              placeholder={
                provider === "gemini"     ? "gemini-1.5-flash" :
                provider === "openai"     ? "gpt-4o-mini" :
                provider === "anthropic"  ? "claude-haiku-4-5-20251001" :
                                            "llama3"
              }
              style={{ width: "100%", padding: "10px 14px", borderRadius: 10, border: "1px solid var(--border)", fontSize: 14, background: "var(--bg)", color: "var(--text)" }}
            />
          </Field>

          {error && (
            <div style={{ padding: "10px 14px", borderRadius: 10, background: "#FEF2F2", color: "#B91C1C", fontSize: 13 }}>{error}</div>
          )}

          <button onClick={onStart} disabled={loading}
            style={{ padding: "13px 24px", borderRadius: 12, background: loading ? "#93A3D8" : "var(--accent)", color: "#fff", fontSize: 15, fontWeight: 600, transition: "background 0.15s" }}>
            {loading ? "Startar…" : "Starta konversation →"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Chat Screen ──────────────────────────────────────────────────────────────

function ChatScreen({ msgs, loading, input, setInput, onSend, onFinish, inputRef, bottomRef, isComplete, systemType, provider }: any) {
  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", maxWidth: 680, margin: "0 auto" }}>

      {/* Header */}
      <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", background: "var(--surface)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 15 }}>Bilverkstad – Boka tid</div>
          <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>
            {systemType === "skill_based" ? "Skill-based" : "Fri LLM"} · {provider}
          </div>
        </div>
        {isComplete && (
          <button onClick={onFinish}
            style={{ padding: "8px 16px", borderRadius: 10, background: "var(--accent)", color: "#fff", fontSize: 13, fontWeight: 600 }}>
            Avsluta & betygsätt
          </button>
        )}
      </div>

      {/* Messages */}
      <div style={{ flex: 1, overflowY: "auto", padding: "20px 16px", display: "flex", flexDirection: "column", gap: 12 }}>
        {msgs.map((m: Msg, i: number) => (
          <div key={i} className="fade-up" style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
            {m.role === "assistant" && (
              <div style={{ width: 30, height: 30, borderRadius: "50%", background: "var(--accent-lt)", border: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14, marginRight: 8, flexShrink: 0, marginTop: 2 }}>
                🔧
              </div>
            )}
            <div style={{
              maxWidth: "75%", padding: "12px 16px", borderRadius: m.role === "user" ? "18px 18px 4px 18px" : "18px 18px 18px 4px",
              background: m.role === "user" ? "var(--user-bg)" : "var(--bot-bg)",
              color: m.role === "user" ? "var(--user-text)" : "var(--text)",
              border: m.role === "assistant" ? "1px solid var(--border)" : "none",
              fontSize: 14, lineHeight: 1.65, whiteSpace: "pre-wrap", boxShadow: "var(--shadow)",
            }}>
              {m.content}
            </div>
          </div>
        ))}

        {loading && (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ width: 30, height: 30, borderRadius: "50%", background: "var(--accent-lt)", border: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14 }}>🔧</div>
            <div style={{ display: "flex", gap: 5, padding: "12px 16px", background: "var(--bot-bg)", border: "1px solid var(--border)", borderRadius: "18px 18px 18px 4px", boxShadow: "var(--shadow)" }}>
              {[0, 1, 2].map(i => (
                <span key={i} style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--muted)", display: "inline-block", animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite` }} />
              ))}
            </div>
          </div>
        )}

        {isComplete && (
          <div className="fade-up" style={{ textAlign: "center", padding: "16px", color: "var(--muted)", fontSize: 13 }}>
            Bokningen är klar! Klicka på "Avsluta & betygsätt" ovan.
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)", background: "var(--surface)", display: "flex", gap: 10 }}>
        <input
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === "Enter" && !e.shiftKey && onSend()}
          disabled={loading || isComplete}
          placeholder={isComplete ? "Konversationen är avslutad" : "Skriv ett meddelande…"}
          style={{ flex: 1, padding: "12px 16px", borderRadius: 12, border: "1px solid var(--border)", fontSize: 14, background: isComplete ? "var(--bg)" : "var(--surface)", color: "var(--text)", transition: "border-color 0.15s" }}
        />
        <button onClick={onSend} disabled={loading || isComplete || !input.trim()}
          style={{ padding: "12px 18px", borderRadius: 12, background: (!input.trim() || loading || isComplete) ? "var(--border)" : "var(--accent)", color: "#fff", fontSize: 15, fontWeight: 600, transition: "background 0.15s", minWidth: 50 }}>
          ↑
        </button>
      </div>
    </div>
  );
}

// ─── Questionnaire ────────────────────────────────────────────────────────────

function QuestionnaireScreen({ answers, onAnswer, onSubmit, sessionId }: any) {
  const allAnswered = UES_QUESTIONS.every(q => answers[q.id]);
  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "24px" }}>
      <div style={{ width: "100%", maxWidth: 520, background: "var(--surface)", borderRadius: "var(--radius)", border: "1px solid var(--border)", padding: "40px 36px", boxShadow: "var(--shadow)" }}>
        <div style={{ marginBottom: 28 }}>
          <div style={{ fontSize: 13, fontWeight: 500, color: "var(--accent)", letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 8 }}>Utvärdering</div>
          <h2 style={{ fontSize: 22, fontWeight: 600, marginBottom: 6 }}>Hur upplevde du konversationen?</h2>
          <p style={{ fontSize: 13, color: "var(--muted)" }}>Session: <code style={{ fontFamily: "'DM Mono', monospace", fontSize: 12 }}>{sessionId.slice(0, 8)}</code></p>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          {UES_QUESTIONS.map(q => (
            <div key={q.id}>
              <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 10, lineHeight: 1.4 }}>{q.label}</div>
              {q.type === "yn" ? (
                <div style={{ display: "flex", gap: 8 }}>
                  {["Ja", "Nej"].map(v => (
                    <button key={v} onClick={() => onAnswer(q.id, v)}
                      style={{ padding: "8px 20px", borderRadius: 8, border: `1.5px solid ${answers[q.id] === v ? "var(--accent)" : "var(--border)"}`, background: answers[q.id] === v ? "var(--accent-lt)" : "transparent", color: answers[q.id] === v ? "var(--accent)" : "var(--text)", fontSize: 14, fontWeight: 500, transition: "all 0.1s" }}>
                      {v}
                    </button>
                  ))}
                </div>
              ) : (
                <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <span style={{ fontSize: 12, color: "var(--muted)", width: 60 }}>Instämmer ej</span>
                  {[1, 2, 3, 4, 5].map(n => (
                    <button key={n} onClick={() => onAnswer(q.id, String(n))}
                      style={{ width: 38, height: 38, borderRadius: 8, border: `1.5px solid ${answers[q.id] === String(n) ? "var(--accent)" : "var(--border)"}`, background: answers[q.id] === String(n) ? "var(--accent)" : "transparent", color: answers[q.id] === String(n) ? "#fff" : "var(--text)", fontSize: 14, fontWeight: 500, transition: "all 0.1s" }}>
                      {n}
                    </button>
                  ))}
                  <span style={{ fontSize: 12, color: "var(--muted)", width: 60, textAlign: "right" }}>Instämmer</span>
                </div>
              )}
            </div>
          ))}

          <button onClick={onSubmit} disabled={!allAnswered}
            style={{ padding: "13px 24px", borderRadius: 12, background: allAnswered ? "var(--accent)" : "var(--border)", color: allAnswered ? "#fff" : "var(--muted)", fontSize: 15, fontWeight: 600, transition: "all 0.15s", marginTop: 8 }}>
            Skicka svar
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Done ─────────────────────────────────────────────────────────────────────

function DoneScreen() {
  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "24px" }}>
      <div style={{ textAlign: "center", maxWidth: 380 }}>
        <div style={{ fontSize: 48, marginBottom: 20 }}>✓</div>
        <h2 style={{ fontSize: 24, fontWeight: 600, marginBottom: 12 }}>Tack för din medverkan!</h2>
        <p style={{ fontSize: 15, color: "var(--muted)", lineHeight: 1.7 }}>
          Dina svar har sparats. Du kan nu stänga den här fliken.
        </p>
      </div>
    </div>
  );
}

// ─── Small UI helpers ─────────────────────────────────────────────────────────

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6, display: "flex", justifyContent: "space-between" }}>
        <span>{label}</span>
        {hint && <span style={{ color: "var(--muted)", fontWeight: 400 }}>{hint}</span>}
      </div>
      {children}
    </div>
  );
}

function ToggleGroup({ options, value, onChange }: { options: { value: string; label: string; desc: string }[]; value: string; onChange: (v: string) => void }) {
  return (
    <div style={{ display: "flex", gap: 8 }}>
      {options.map(o => (
        <button key={o.value} onClick={() => onChange(o.value)}
          style={{ flex: 1, padding: "10px 12px", borderRadius: 10, border: `1.5px solid ${value === o.value ? "var(--accent)" : "var(--border)"}`, background: value === o.value ? "var(--accent-lt)" : "transparent", color: value === o.value ? "var(--accent)" : "var(--text)", textAlign: "left", transition: "all 0.1s" }}>
          <div style={{ fontWeight: 600, fontSize: 13 }}>{o.label}</div>
          <div style={{ fontSize: 11, color: value === o.value ? "var(--accent)" : "var(--muted)", marginTop: 2 }}>{o.desc}</div>
        </button>
      ))}
    </div>
  );
}

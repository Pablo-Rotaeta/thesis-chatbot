const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type SystemType = "unconstrained" | "skill_based";
export type Provider   = "ollama" | "gemini" | "openai" | "anthropic";

export interface StartResponse {
  session_id: string;
  opening_message: string;
  system_type: SystemType;
  provider: Provider;
  model: string;
}

export interface MessageResponse {
  reply: string;
  current_step: string | null;
  slots_filled: Record<string, string>;
  is_complete: boolean;
}

export async function startSession(
  system_type: SystemType,
  llm_provider: Provider,
  llm_model?: string,
): Promise<StartResponse> {
  const r = await fetch(`${API}/api/chat/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ system_type, llm_provider, llm_model }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function sendMessage(
  session_id: string,
  message: string,
): Promise<MessageResponse> {
  const r = await fetch(`${API}/api/chat/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id, message }),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function endSession(
  session_id: string,
  task_success?: boolean,
): Promise<void> {
  await fetch(`${API}/api/chat/end`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id, task_success }),
  });
}
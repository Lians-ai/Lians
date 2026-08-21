export type AppKind = "challenge" | "vote" | "quiz" | "leaderboard" | "tracker" | "list";

export type AppConfig = {
  accent: string;
  accentSoft: string;
  emoji: string;
  options: string[];
  unit: string;
  target: number;
  questions: Array<{ question: string; options: string[] }>;
};

export type MiniApp = {
  id: string;
  kind: AppKind;
  title: string;
  description: string;
  prompt: string;
  config: AppConfig;
  remixOf: string | null;
  createdAt: number;
};

export type AppEvent = {
  id: string;
  appId: string;
  actor: string;
  action: string;
  value: string;
  metadata: Record<string, string | number | boolean>;
  createdAt: number;
};

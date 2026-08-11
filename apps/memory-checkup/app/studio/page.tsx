import type { Metadata } from "next";
import StudioClient from "./StudioClient";
import "./studio.css";

export const metadata: Metadata = {
  title: "Lians Memory Studio | See What Your AI Remembers",
  description:
    "Search, correct, pin, or retire the memories your AI uses from one clear workspace.",
};

export default function StudioPage() {
  return <StudioClient />;
}

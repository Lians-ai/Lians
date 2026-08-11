import type { Metadata } from "next";
import StudioClient from "./StudioClient";
import "./studio.css";

export const metadata: Metadata = {
  title: "Lians Memory Studio | Govern What AI Knows",
  description:
    "Inspect, explain, correct, and verify the memory behind consequential AI decisions without rewriting history.",
};

export default function StudioPage() {
  return <StudioClient />;
}

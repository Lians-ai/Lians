import type { Metadata } from "next";
import StudioClient from "./StudioClient";
import "./studio.css";

export const metadata: Metadata = {
  title: "Lians Studio | Inspect and govern agent memory",
  description:
    "Inspect, explain, correct, and verify durable AI memory without rewriting its history.",
};

export default function StudioPage() {
  return <StudioClient />;
}

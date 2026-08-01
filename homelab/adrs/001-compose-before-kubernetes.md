# ADR 001: Docker Compose before Kubernetes

Status: accepted

## Decision

The first integration lab uses Docker Compose with pinned service tags and
file-provisioned configuration. Kubernetes is a later compatibility target.

## Why

Compose runs on the founders' existing machine, keeps partner scenarios easy to
reset and screen-share, and makes failures attributable to the product contract
rather than cluster administration. The existing Lians Kubernetes manifests
remain valuable when a customer actually asks for Kubernetes-shaped proof.

## Consequence

This lab proves application and telemetry integration, not node rescheduling,
multi-zone availability, or Kubernetes policy behavior.

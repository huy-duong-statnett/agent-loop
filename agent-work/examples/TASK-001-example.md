---
id: TASK-001
title: Add health-check endpoint
repos: [backend]
---

# Add health-check endpoint

## Goal
Expose `GET /health` so the load balancer can check that the service is alive.

## Context
- Routes live in the backend's existing router modules; follow the pattern of an existing simple route.
- Must not require authentication.

## Contract
```
GET /health
200 -> { "status": "ok" }
```

## Acceptance criteria
- [ ] `GET /health` returns HTTP 200 and `{"status": "ok"}`
- [ ] A test covers the endpoint
- [ ] Existing tests still pass

## Out of scope
- Database connectivity checks, metrics, authentication changes.

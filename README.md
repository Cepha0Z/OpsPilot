# OpsPilot

**Open-source AI agent for IT operations.**

OpsPilot turns natural-language IT requests into validated, approval-aware workflows and executes them through Microsoft Graph.

## What It Does

Instead of navigating multiple admin portals, you can ask OpsPilot things like:

> What licenses does John Doe have?

> Create an account for Rat Joe in the IT department.

> Find unread emails from the last 7 days.

> Draft an email to John Doe explaining the issue.

OpsPilot uses an AI planner to turn the request into structured tasks, validates those tasks, executes them through Microsoft Graph, and presents the results in a human-readable format.

## Architecture

```text
Natural Language Request
          |
          v
     AI Planner
          |
          v
   Structured Plan
          |
          v
   Plan Validation
          |
          v
   Approval Required?
       /       \
     Yes        No
      |          |
      v          |
   Approval      |
      |          |
      +-----+----+
            |
            v
        Executor
            |
            v
     Microsoft Graph
            |
            v
    Structured Results
            |
            v
    Result Synthesis
            |
            v
       User Response

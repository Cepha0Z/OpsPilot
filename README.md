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
```

## Current Capabilities

### Users

- Create users
- Look up users
- List users
- Enable/disable accounts
- Reset passwords
- Revoke sessions
- Delete users
- View assigned licenses
- Generate user/account activity reports

### Mail

- Search email
- Find unread email
- Retrieve email
- Summarize email
- Generate replies
- Draft email with approval
- Send email
- Read/write mailbox data

### Licensing

- List available licenses
- View user licenses
- Assign licenses through approved workflows

### Workflow Engine

- Typed tool contracts
- Dependency-aware execution
- Approval workflows
- Persistent workflow state
- SQLite-backed execution storage
- Idempotency support
- Workflow deadlines
- Bounded execution capacity
- Parallel execution of independent read-only tasks
- Sequential handling of writes and approval-gated operations
- Partial-completion handling
- Failure propagation
- Restart recovery

## Safety

OpsPilot is designed around the principle that **AI should plan actions, not silently execute everything it thinks of.**

Consequential operations can require explicit approval before execution.

Sensitive information is protected from normal user-facing output and logs, including:

- Passwords
- API keys
- Access tokens
- Verification codes
- Raw provider payloads
- Internal Graph data

Drafted emails require approval before an actual Outlook draft is created.

Sending mail remains a separate explicit operation.

## Testing

OpsPilot includes automated tests covering:

- Tool contracts
- Planner validation
- Workflow execution
- Approval flows
- Dependency chains
- Failure handling
- Partial completion
- Parallel reads
- Result synthesis
- Sensitive-data redaction
- Frontend response handling
- Fake Microsoft Graph workflows

The current test suite contains **67 automated tests**.

Fake Graph fixtures allow complex workflows to be tested without making real Microsoft Graph requests.

## Technology

- Python
- Microsoft Graph API
- Gemini
- SQLite
- JavaScript
- HTML/CSS

## Microsoft Graph

OpsPilot operates against Microsoft 365 through Microsoft Graph.

The capabilities available to an installation depend on the Graph permissions granted to its Entra application.

Current development capabilities include areas such as:

- Users
- Organization information
- Mail
- User management
- Licensing

Additional Microsoft Graph capabilities can be added as the project grows.

## Configuration

Create a local `.env` file containing your environment-specific configuration.

Example:

```env
GEMINI_API_KEY=your-gemini-api-key

NEBULOUS_NEW_USER_TEMPORARY_PASSWORD=your-temporary-password
```

**Never commit `.env` or real credentials to the repository.**

For production deployments, use an appropriate secret-management solution.

## Installation

Clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/opspilot.git
cd opspilot
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create your `.env` file and configure the required credentials.

## Running

Start the application using the project's server configuration.

For example:

```bash
python server.py
```

> The exact startup command may vary depending on your local configuration.

## Running Tests

Run the complete test suite with:

```bash
python -m unittest discover -s tests -v
```

## Roadmap

Planned areas include:

- OneDrive / SharePoint operations
- Richer IT activity reporting
- Additional Microsoft Graph capabilities
- More IT workflow templates
- More sophisticated planner capabilities
- Production authentication
- Additional enterprise integrations

## Current Status

OpsPilot is an **active open-source project** and is not yet intended as a production-ready enterprise deployment.

Authentication and production authorization are intentionally deferred while the core IT automation engine is being developed.

Use appropriate caution when granting Microsoft Graph **application permissions**, especially permissions that allow modifying users, mailboxes, or organizational data.

## Contributing

Contributions are welcome.

If you're interested in adding a new IT capability:

1. Add the Graph integration.
2. Define the corresponding `ToolSpec`.
3. Add validation and safe result handling.
4. Add appropriate approval requirements.
5. Add fake-Graph tests.
6. Add regression tests.
7. Update the documentation.

## License

This project is open source.

Add your chosen license to this repository.

---

**OpsPilot — Turn IT requests into controlled, executable workflows.**

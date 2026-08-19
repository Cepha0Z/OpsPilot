OpsPilot

Open-source AI agent for IT operations.

OpsPilot turns natural-language IT requests into validated, approval-aware workflows and executes them through Microsoft Graph.

✨ What It Does

Instead of navigating multiple admin portals, you can ask OpsPilot things like:

What licenses does John Doe have?
Create an account for Rat Joe in the IT department.
Find unread emails from the last 7 days.
Draft an email to John Doe explaining the issue.

OpsPilot uses an AI planner to turn the request into structured tasks, validates those tasks, executes them through Microsoft Graph, and presents the result in a human-readable format.

🧠 Architecture
Natural Language Request
          │
          ▼
     AI Planner
          │
          ▼
   Structured Plan
          │
          ▼
   Plan Validation
          │
          ▼
   Approval Required?
      │          │
     Yes         No
      │          │
      ▼          │
    Approval     │
      │          │
      └────┬─────┘
           ▼
       Executor
           │
           ▼
    Microsoft Graph
           │
           ▼
    Structured Results
           │
           ▼
   AI / Result Synthesis
           │
           ▼
      User Response
🚀 Current Capabilities
👤 Users
Create users
Look up users
List users
Enable/disable accounts
Reset passwords
Revoke sessions
Delete users
View assigned licenses
Generate account/activity reports
📧 Mail
Search email
Find unread email
Retrieve email
Summarize email
Generate replies
Draft email with approval
Send email
Read/write mailbox data
💳 Licensing
List available licenses
View user licenses
Assign licenses through approved workflows
⚙️ Workflow Engine
Structured typed tool contracts
Dependency-aware execution
Approval workflows
Persistent workflow state
SQLite-backed execution storage
Idempotency support
Workflow deadlines
Bounded execution capacity
Parallel execution of independent read-only tasks
Sequential handling of writes and approval-gated operations
Partial-completion handling
Failure propagation
Restart recovery
🔐 Safety

OpsPilot is designed around the principle that AI should plan actions, not silently execute everything it thinks of.

Consequential operations can require explicit approval before execution.

Sensitive information is also protected from normal user-facing output and logs, including:

Passwords
API keys
Access tokens
Verification codes
Raw provider payloads
Internal Graph data

Drafted emails require approval before an actual Outlook draft is created.

Sending mail remains a separate explicit operation.

🧪 Testing

OpsPilot currently has a comprehensive automated test suite covering:

Tool contracts
Planner validation
Workflow execution
Approval flows
Dependency chains
Failure handling
Partial completion
Parallel reads
Result synthesis
Sensitive-data redaction
Frontend response handling
Fake Microsoft Graph workflows

The project currently has 67 automated tests.

Fake Graph fixtures allow complex workflows to be tested without making real Microsoft Graph requests.

🛠️ Technology
Python
Microsoft Graph API
Gemini
SQLite
JavaScript
HTML/CSS
🔑 Microsoft Graph

OpsPilot is designed to operate against Microsoft 365 through Microsoft Graph.

The capabilities available to an installation depend on the Graph permissions granted to its Entra application.

For example, the current development environment uses permissions for areas including:

Users
Organization information
Mail
User management

Additional Microsoft Graph capabilities can be added as the project grows.

⚙️ Configuration

Create a local .env file containing your environment-specific configuration.

Example:

GEMINI_API_KEY=your-gemini-api-key


NEBULOUS_NEW_USER_TEMPORARY_PASSWORD=your-temporary-password

Never commit .env or real credentials to the repository.

Use a secret manager or secure deployment configuration for production environments.

▶️ Running

Clone the repository:

git clone https://github.com/YOUR_USERNAME/opspilot.git
cd opspilot

Install dependencies:

pip install -r requirements.txt

Configure your environment:

# Create .env and add your configuration

Start the application according to the project's server configuration.

🧪 Running Tests
python -m unittest discover -s tests -v
🗺️ Roadmap

Planned areas include:

More Microsoft Graph capabilities
OneDrive / SharePoint operations
Richer IT activity reporting
Additional IT workflow templates
More sophisticated planner capabilities
Production authentication
Additional enterprise integrations
⚠️ Current Status

OpsPilot is an active open-source project and is not yet intended as a production-ready enterprise deployment.

Authentication and production authorization are intentionally deferred while the core IT automation engine is being developed.

Use appropriate caution when granting Microsoft Graph application permissions, especially permissions that allow modifying users, mailboxes, or organizational data.

🤝 Contributing

Contributions are welcome.

If you're interested in adding a new IT capability:

Add the Graph integration.
Define the corresponding ToolSpec.
Add validation and safe result handling.
Add appropriate approval requirements.
Add fake-Graph tests.
Add regression tests.
Update the documentation.
📄 License

This project is open source. Add your chosen license here.

OpsPilot — Turn IT requests into controlled, executable workflows.

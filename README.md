# Agentic AI Workflow POC

Agentic AI Workflow POC is a FastAPI-based automation application that allows users to give natural language instructions and convert them into executable workflow actions using Gmail, Slack, OpenAI, and n8n.

The goal of this project is to let a user type requests such as:

* "Summarize my latest Gmail emails and display here"
* "Read Slack updates from #engineering and unread Gmail emails and show here"
* "Generate a project update and send it to Slack channel #any channel name"
* "Send a follow-up email using Gmail"

The backend understands the request, checks required connections, builds workflow steps, creates an n8n workflow, activates it, calls the webhook, and returns the final response back to the chat UI.

---

## 1. Project Purpose

This project is built as a proof of concept for a dynamic AI workflow system.

Instead of manually creating workflows inside n8n every time, the user can describe the task in normal English. The system then:

1. Understands the user request.
2. Decides whether the request needs a workflow.
3. Checks whether Gmail or Slack connections are available.
4. Creates structured workflow steps.
5. Builds n8n-compatible workflow JSON.
6. Creates the workflow in n8n.
7. Activates the workflow.
8. Calls the production webhook.
9. Displays the result back to the user.

---

## 2. Main Features

### Natural Language Workflow Creation

Users can type normal English instructions. The system converts those instructions into structured automation steps.

Example:

```text
Summarize my unread Gmail emails and send the summary to Slack #general
```

The application understands that:

* Gmail is the source.
* Slack is the destination.
* AI summarization is required.
* Gmail and Slack connections are needed.

---

### Gmail Integration

The project supports Gmail as both a source and destination.

Gmail can be used to:

* Read emails
* Search emails
* Read unread emails
* Send emails
* Use Gmail as part of a multi-step workflow

The project is designed so that each logged-in user has their own Gmail connection.

---

### Slack Integration

The project supports Slack as both a source and destination.

Slack can be used to:

* Read channel updates
* Search Slack messages
* Send messages to Slack channels

Each logged-in user can connect their own Slack workspace/account.

---

### OpenAI Planner

The OpenAI model is used to understand the user's natural language request.

The planner decides whether the user request is:

* A direct answer
* A follow-up question
* A workflow request
* A request that cannot be executed

---

### n8n Workflow Generation

The backend creates n8n-style workflow JSON dynamically.

It can create nodes for:

* Webhook trigger
* Gmail
* Slack
* OpenAI/LLM processing
* Chat response

---

### Per-User Connection Handling

The project is designed to avoid global Gmail or Slack fallback.

Each user's Gmail and Slack connections are stored separately under that user's account.

This means one user's Gmail or Slack credentials should not be used for another user.

---

## 3. Tech Stack

| Area              | Technology            |
| ----------------- | --------------------- |
| Backend           | FastAPI               |
| Language          | Python                |
| Workflow Engine   | n8n                   |
| AI Model          | OpenAI                |
| Email Integration | Gmail OAuth           |
| Chat Integration  | Slack OAuth           |
| Frontend          | HTML, CSS, JavaScript |
| API Calls         | Requests library      |

---

## 4. Project Structure

```text
Agentic-AI-POC/
│
├── app.py
├── main.py
├── planner.py
├── resolver.py
├── composer.py
├── builder.py
├── builder_preview.py
├── n8n_client.py
├── connection_manager.py
├── credential_manager.py
├── tool_registry.py
├── requirements.txt
├── config.example.py
├── README.md
├── .gitignore
│
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── style.css
│
└── data/
    ├── allowed_users.example.json
    ├── connections.example.json
    └── n8n_credentials.example.json
```

---

## 5. File Explanation

### `app.py`

This is the main FastAPI application file.

It handles:

* Backend API routes
* Google login
* User sessions
* Gmail connection routes
* Slack connection routes
* Chat requests
* Workflow creation
* Workflow activation
* Calling n8n production webhook
* Returning results to the frontend

---

### `main.py`

This is the main workflow pipeline controller.

It controls the internal process:

```text
planner → resolver → composer → builder_preview → builder → n8n_client
```

It does not directly execute Slack or Gmail actions. It prepares the workflow and sends it to n8n.

---

### `planner.py`

This file understands the user's message.

It decides:

* Is this a normal question?
* Is this a workflow request?
* Is more information needed?
* Which tools are required?
* Is Gmail the source?
* Is Slack the destination?
* Is AI processing required?

Example:

```text
Read my unread Gmail emails and display here
```

The planner identifies:

* Source: Gmail
* Operation: Read
* Filter: Unread
* Destination: Chat UI

---

### `resolver.py`

This file validates the planner output before creating a workflow.

It checks:

* Is the requested tool supported?
* Can the tool be used as a source?
* Can the tool be used as a destination?
* Is the requested operation allowed?
* Is Gmail connected for this user?
* Is Slack connected for this user?
* Are required n8n credentials available?

---

### `composer.py`

This file converts planner output into clean workflow steps.

Example workflow steps:

```text
Step 1: Read Gmail emails
Step 2: Summarize using AI
Step 3: Return result to chat
```

The composer does not call Gmail, Slack, n8n, or OpenAI directly. It only prepares structured workflow steps.

---

### `builder_preview.py`

This file creates a safe preview of how the workflow will map to n8n nodes.

It does not execute anything.

It is useful for debugging and understanding which n8n nodes will be created.

---

### `builder.py`

This file builds the real n8n workflow JSON.

It attaches the correct n8n credentials for:

* Gmail
* Slack
* OpenAI

It also builds the n8n nodes and connections between them.

---

### `n8n_client.py`

This file communicates with the n8n API.

It can:

* Test n8n connection
* List workflows
* Create workflows
* Activate workflows
* Find webhook path
* Call production webhook

---

### `connection_manager.py`

This file manages Gmail and Slack connection data for each logged-in user.

It reads and writes:

```text
data/connections.json
```

This file should stay local and should not be uploaded to GitHub because it can contain sensitive tokens.

---

### `credential_manager.py`

This file manages n8n credential mappings.

It reads and writes:

```text
data/n8n_credentials.json
```

This file connects a logged-in app user to their Gmail/Slack n8n credential IDs.

This file should stay local and should not be uploaded to GitHub.

---

### `tool_registry.py`

This file defines which tools the app supports.

Currently supported tools include:

* Gmail
* Slack
* Chat UI
* Web
* File

It also defines whether each tool can be used as:

* Source
* Destination
* Both

---

### `requirements.txt`

This file contains the Python packages needed to run the project.

Example:

```text
openai
fastapi
uvicorn
requests
```

---

## 6. Required Setup

Before running the project, you need:

1. Python installed
2. n8n running locally
3. OpenAI API key
4. Google OAuth app credentials
5. Slack OAuth app credentials
6. Required Python packages installed

---

## 7. Installation Steps

### Step 1: Clone or Download the Project

```bash
git clone <your-github-repo-url>
cd Agentic-AI-POC
```

Or download the ZIP file from GitHub and extract it.

---

### Step 2: Install Python Dependencies

```bash
pip install -r requirements.txt
```

---

### Step 3: Create Local `config.py`

Do not upload your real `config.py` to GitHub.

Create a local file:

```text
config.py
```

Use `config.example.py` as a reference.

Example:

```python
# Model API
OPENAI_API_KEY = "your_openai_api_key"
PLANNER_MODEL = "gpt-5.4-mini"

# n8n API
N8N_BASE_URL = "http://localhost:5678"
N8N_API_KEY = "your_n8n_api_key"

# Slack OAuth
SLACK_CLIENT_ID = "your_slack_client_id"
SLACK_CLIENT_SECRET = "your_slack_client_secret"
SLACK_REDIRECT_URI = "http://127.0.0.1:8000/auth/slack/callback"

# Google OAuth
GOOGLE_CLIENT_ID = "your_google_client_id"
GOOGLE_CLIENT_SECRET = "your_google_client_secret"
GOOGLE_REDIRECT_URI = "http://127.0.0.1:8000/auth/gmail/callback"

MAX_OUTPUT_TOKENS = 800
TEMPERATURE = 0
```

---

### Step 4: Create Local `data/allowed_users.json`

Create this file locally:

```text
data/allowed_users.json
```

Example:

```json
{
  "allowed_users": [
    "your-email@gmail.com"
  ],
  "allowed_domains": []
}
```

Only users listed in this file will be allowed to use the application.

---

### Step 5: Start n8n

Start n8n locally.

The project expects n8n to run at:

```text
http://localhost:5678
```

Make sure your n8n API key is active and added to `config.py`.

---

### Step 6: Run the FastAPI App

```bash
uvicorn app:app --reload
```

The backend will start at:

```text
http://127.0.0.1:8000
```

---

## 8. Frontend

The frontend files are stored inside:

```text
frontend/
```

Expected files:

```text
frontend/index.html
frontend/app.js
frontend/style.css
```

The backend serves frontend files using FastAPI static files.

---

## 9. Important Files Not to Upload

Do not upload these real local files to GitHub:

```text
config.py
data/connections.json
data/n8n_credentials.json
data/allowed_users.json
logs/
```

These files can contain private information such as:

* API keys
* OAuth tokens
* Gmail account details
* Slack workspace tokens
* n8n credential IDs
* User account information

---

## 10. Safe Example Files to Upload

Instead of uploading real private files, upload example files:

```text
config.example.py
data/allowed_users.example.json
data/connections.example.json
data/n8n_credentials.example.json
```

These files show the required structure without exposing real secrets.

---

## 11. `.gitignore`

This project should include a `.gitignore` file to prevent private files from being uploaded.

Recommended `.gitignore`:

```gitignore
config.py

data/connections.json
data/n8n_credentials.json
data/allowed_users.json

logs/
__pycache__/
*.pyc

venv/
.venv/
node_modules/
```

---

## 12. Example User Requests

Here are example prompts the user can type into the app:

```text
What is Slack?
```

This should return a direct answer without creating a workflow.

```text
Summarize my latest Gmail emails and display here
```

This should read Gmail, summarize using AI, and display the result in the chat UI.

```text
Read Slack updates from #general and display here
```

This should read Slack messages from the selected channel and display the result.

```text
Summarize unread Gmail emails and send to Slack #project-updates
```

This should read unread Gmail emails, summarize them, and post the summary to Slack.

```text
Create a project update and send to Slack #general
```

This should generate a project update using AI and send it to Slack.

---

## 13. How the Workflow Works

The application follows this process:

```text
User message
   ↓
Planner understands the request
   ↓
Resolver checks required tools and connections
   ↓
Composer creates workflow steps
   ↓
Builder preview creates safe node mapping
   ↓
Builder creates real n8n workflow JSON
   ↓
n8n client creates workflow in n8n
   ↓
App activates workflow
   ↓
App calls production webhook
   ↓
Result returns to frontend chat
```

---

## 14. Security Notes

This project uses OAuth tokens and API credentials.

Important safety rules:

1. Never upload real API keys to GitHub.
2. Never upload real Gmail OAuth tokens.
3. Never upload real Slack OAuth tokens.
4. Never upload real n8n credential IDs.
5. Keep `config.py` private.
6. Keep `data/connections.json` private.
7. Keep `data/n8n_credentials.json` private.
8. If secrets are accidentally exposed, revoke and regenerate them immediately.

---

## 15. Current Limitations

This is a proof-of-concept project.

Current limitations may include:

* n8n must be running locally.
* Gmail and Slack OAuth setup must be completed correctly.
* The app currently depends on local configuration files.
* Workflow execution depends on valid n8n credentials.
* Some workflows may require follow-up questions if the user does not provide enough details.
* Empty or missing credential files may stop workflow creation.
* The current setup is mainly for local development and testing.

---

## 16. Future Improvements

Possible future improvements:

* Add hosted deployment support.
* Add database storage instead of local JSON files.
* Add multi-account Gmail support.
* Add more tools such as Google Calendar, Microsoft Teams, Jira, and Notion.
* Add workflow history page.
* Add admin dashboard.
* Add better error messages in the frontend.
* Add Docker support.
* Add production-ready authentication.
* Add cloud n8n support.

---

## 17. Project Status

This project is currently a working proof of concept for dynamic AI-based workflow generation using FastAPI, OpenAI, Gmail, Slack, and n8n.

It demonstrates how a natural language request can be converted into a real automation workflow.
